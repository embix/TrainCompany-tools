from __future__ import annotations

from dataclasses import dataclass, field, InitVar
from enum import Enum
from typing import List, Dict, Any, Optional, Set

import networkx as nx

from structures.pronouns import Pronouns, ErIhmPronouns, SieIhrPronouns
from validation.graph import get_path_suggestion, PathSuggestionConfig
from validation.shortest_paths import without_trivial_nodes, get_shortest_path


@dataclass(frozen=True)
class TcNeededCapacity:
    name: str
    value: int | None = field(default=None)

    @staticmethod
    def from_dict(needed_capacity: Dict[str, Any]) -> TcNeededCapacity:
        return TcNeededCapacity(
            name=needed_capacity['name'],
            value=needed_capacity['value']
        )


@dataclass
class Task:
    name: str
    descriptions: List[str]
    stations: List[str]
    neededCapacity: List[TcNeededCapacity]
    group: int = field(default=1)
    service: int = field(default=4)
    graph: InitVar[Optional[nx.Graph]] = None
    path_suggestion_config: InitVar[PathSuggestionConfig] = None

    pathSuggestion: Optional[List[str]] = field(default=None, repr=False, hash=False, init=False)

    def __post_init__(self, graph: Optional[nx.Graph], path_suggestion_config: PathSuggestionConfig):
        if not path_suggestion_config:
            path_suggestion_config = PathSuggestionConfig()
        if graph:
            object.__setattr__(self, 'pathSuggestion', get_path_suggestion(graph, self.stations,
                                                                           config=path_suggestion_config))

    def to_dict(self, add_suggestion: bool = False) -> Dict[str, Any]:
        task = self.__dict__
        task['neededCapacity'] = [
            {name: value for name, value in needed_capacity.__dict__.items() if value} for needed_capacity in
            self.neededCapacity
        ]
        if not add_suggestion:
            task.pop('pathSuggestion', None)
        return task

    def uses_sfs(self, graph: nx.Graph) -> bool:
        stations = get_shortest_path(graph, self.stations)
        for station_from, station_to in zip(stations, stations[1:]):
            edge = graph[station_from][station_to]
            if 'group' not in edge:
                # By default, the group is 0 or at least not 2
                return False
            if edge['group'] == 2:
                return True
        else:
            return False


class ServiceLevel(Enum):
    HIGH_SPEED = 0
    INTERCITY = 1
    REGIONAL = 2
    COMMUTER = 3
    SPECIAL = 4
    FREIGHT_IMPORTANT = 10
    FREIGHT = 11


class GattungTask(Task):
    gattung: str = ''
    gattung_long: str = ''
    pronouns: Pronouns
    service: ServiceLevel = ServiceLevel.SPECIAL
    needed_capacity: List[TcNeededCapacity] = [
        TcNeededCapacity(
            name='passengers',
            value=0
        )
    ]

    def __init__(self,
                 line: str,
                 line_name: Optional[str] = None,
                 name_pronouns: Optional[Pronouns] = None,
                 needed_capacities: List[TcNeededCapacity] | None = None,
                 *args, **kwargs):
        super().__init__(
            name="{} von %s nach %s".format(line_name) if line_name
            else "{} {} von %s nach %s".format(self.__class__.gattung, line) if line
            else "{} von %s nach %s".format(self.__class__.gattung),
            descriptions=self._generate_descriptions(line, line_name, name_pronouns),
            neededCapacity=needed_capacities if needed_capacities is not None else self.__class__.needed_capacity,
            service=self.__class__.service.value,
            *args, **kwargs
        )

    def add_sfs_description(self, graph: nx.Graph):
        if self.uses_sfs(graph):
            self.descriptions.append(
                "Bringe {} {} pünktlich über die SFS von %s nach %s".format(self.__class__.pronouns.articles.accusative,
                                                                            self.__class__.gattung_long)
            )

    def _generate_descriptions(self, line: Optional[str] = None,
                               line_name: Optional[str] = None,
                               name_pronouns: Optional[Pronouns] = None) -> List[str]:
        if line:
            descriptions = [
                "Bringe {} {} der Linie {} von %s nach %s.".format(self.__class__.pronouns.articles.accusative,
                                                                   self.__class__.gattung_long, line),
                "Bring die Fahrgäste in {} {} {} pünktlich nach %2$s.".format(self.__class__.pronouns.articles.dative,
                                                                              self.__class__.gattung, line),
                "Fahre {} {} störungsfrei nach %2$s.".format(self.__class__.pronouns.articles.accusative,
                                                             self.__class__.gattung)
            ]
        else:
            descriptions = [
                "Bringe {} {} von %s nach %s.".format(self.__class__.pronouns.articles.accusative,
                                                      self.__class__.gattung_long),
                "Bring die Fahrgäste in {} {} pünktlich nach %2$s.".format(self.__class__.pronouns.articles.dative,
                                                                           self.__class__.gattung)
                                                                    .replace('in dem', 'im'),
                "Fahre {} {} störungsfrei nach %2$s.".format(self.__class__.pronouns.articles.accusative,
                                                             self.__class__.gattung)
            ]
        if line_name and name_pronouns:
            descriptions.extend([
                "Bring {} {} von %s nach %s.".format(name_pronouns.articles.accusative, line_name)
            ])
        return descriptions


class SbahnTask(GattungTask):
    gattung = 'S'
    gattung_long = 'S-Bahn'
    pronouns = SieIhrPronouns()
    service = ServiceLevel.COMMUTER


class RbTask(GattungTask):
    gattung = 'RB'
    gattung_long = 'Regionalbahn'
    pronouns = SieIhrPronouns()
    service = ServiceLevel.COMMUTER


class ReTask(GattungTask):
    gattung = 'RE'
    gattung_long = 'Regionalexpress'
    pronouns = ErIhmPronouns()
    service = ServiceLevel.REGIONAL


class TerTask(ReTask):
    gattung = "TER"
    gattung_long = "TER"


class IreTask(ReTask):
    gattung = 'IRE'
    gattung_long = 'Interregio-Express'
    pronouns = ErIhmPronouns()


class IcTask(GattungTask):
    gattung = 'IC'
    gattung_long = 'Intercity'
    pronouns = ErIhmPronouns()
    service = ServiceLevel.INTERCITY


class OtcTask(IcTask):
    gattung = 'OTC'
    gattung_long = "OUIGO Train Classique"


class OgvTask(IcTask):
    gattung = "OUIGO"
    gattung_long = "OUGIO"


class IrTask(IcTask):
    gattung = 'IR'
    gattung_long = 'Interregio'


class EcTask(IcTask):
    gattung = 'EC'
    gattung_long = 'Eurocity'
    pronouns = ErIhmPronouns()


class IceTask(GattungTask):
    gattung = 'ICE'
    gattung_long = 'Intercity-Express'
    pronouns = ErIhmPronouns()
    service = ServiceLevel.HIGH_SPEED


class TgvTask(IceTask):
    gattung = 'TGV'
    gattung_long = 'TGV'


class FrTask(IceTask):
    gattung = "FR"
    gattung_long = "Frecciarossa"


class IceSprinterTask(IceTask):
    gattung = 'ICE-Sprinter'
    gattung_long = 'ICE-Sprinter'
    pronouns = ErIhmPronouns()


class EceTask(IceTask):
    gattung = 'ECE'
    gattung_long = 'Eurocity-Express'
    pronouns = ErIhmPronouns()


class NjTask(IcTask):
    gattung = "NJ"
    gattung_long = "Nightjet"
    pronouns = ErIhmPronouns()
    needed_capacity = [
        TcNeededCapacity(
            name="passengers"
        ),
        TcNeededCapacity(
            name="beds"
        )
    ]


class AmtrakTask(NjTask):
    gattung = "AT"
    gattung_long = "Amtrak"
    needed_capacity = NjTask.needed_capacity + [TcNeededCapacity(name="bistroseats")]


# Not optimal algorithm to merge multiple tasks into less tasks
def merge_task_dicts(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(tasks) == 1:
        return tasks
    else:
        for task in tasks:
            for key in ['group', 'neededCapacity', 'name', 'descriptions', 'stations']:
                if key not in task:
                    task[key] = None
        merged_tasks = [tasks[0]]
        # try to merge the other tasks
        for task in tasks[1:]:
            overlap = [0 for _ in merged_tasks]
            # Merge if at least group, and neededCapacity are equal and if there are no objects in the new task
            for index, merge_task in enumerate(merged_tasks):
                if 'objects' not in task \
                   and task['group'] == merge_task['group'] \
                   and task['neededCapacity'] == merge_task['neededCapacity']\
                   and (
                    task['name'] == merge_task['name']
                    or task['descriptions'] == merge_task['descriptions']
                    or task['stations'] == merge_task['stations']
                   ):
                    for key, value in task.items():
                        if merge_task[key] == value:
                            overlap[index] += 1
            # Now we have a mapping from task index to overlap (i.e. shared properties)
            # Merge with the best one
            if max(overlap) != 0:
                merge_task = merged_tasks[overlap.index(max(overlap))]
                if 'objects' not in merge_task:
                    merge_task['objects'] = []
                sub_task: Dict[str, Any] = {}
                for key, value in task.items():
                    if merge_task[key] != value:
                        # Add to the sub_task, because we can't reuse it
                        sub_task[key] = value
                merge_task['objects'].append(sub_task)
            else:
                # No mergeable task found
                merged_tasks.append(task)
        for task in merged_tasks:
            cleanup_task(task)
    return merged_tasks


def cleanup_task(task: Dict[str, Any]):
    for key, value in task.copy().items():
        if value is None:
            task.pop(key)
    if 'objects' in task:
        for task in task['objects']:
            cleanup_task(task)


def extract_remaining_subtask_from_task(task: Dict[str, Any]):
    if 'objects' in task and task['objects']:
        subtask_keys = (set(subtask.keys()) for subtask in task['objects'])
        keys_in_all_subtasks = subtask_keys.__next__()
        for other_subtask_keys in subtask_keys:
            keys_in_all_subtasks.intersection_update(other_subtask_keys)
        new_subtask: Dict[str, Any] = {}
        for key in keys_in_all_subtasks:
            new_subtask[key] = task.pop(key)
        if new_subtask:
            task['objects'].append(new_subtask)
