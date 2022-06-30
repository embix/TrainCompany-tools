from __future__ import annotations

import logging
import statistics
from typing import List, Optional, Dict

from importer import CsvImporter
from structures import Station
from structures.route import CodeWaypoint, Route, Track, TrackKind, Path
from structures.station import iter_stations_by_codes_reverse, StreckenKilometer


class DbTrassenfinderImporter(CsvImporter[CodeWaypoint]):
    def __init__(self):
        super().__init__(
            delimiter=';',
            encoding='cp1252',
            skip_first_line=True
        )

    def deserialize(self, entry: List[str]) -> CodeWaypoint:
        waypoint = CodeWaypoint(
            distance_from_start=float(entry[0].replace(',', '.')),
            code=entry[2].replace('  ', ' '),
            is_stop='Kundenhalt' in entry[17],
            next_route_number=int(entry[3]) if entry[3] else None
        )
        return waypoint


def invalid_track(route_number: int) -> Track:
    logging.warning("Unbekannte Streckennr.: {}. Kann Elektrifizierung, Streckenart nicht identifizieren.".format(route_number))
    return Track(
        electrified=False,
        kind=TrackKind.UNKNOWN,
        length=0,
        route_number=route_number,
    )


def track_from_path(route_number: int,
                    last_known_segment: Optional[Track],
                    to_km: Optional[StreckenKilometer],
                    path_data: Dict[int, Path],
                    code_start: Optional[str] = None,
                    code_end: Optional[str] = None) -> Track:
    if not code_start and not code_end:
        logging.warning("Kann kein Segment feststellen.\n"
                        "    Übernehme Daten zu Elektrifizierung, Streckenklasse vom letzten Segment")
    else:
        logging.warning("Kann kein Segment feststellen zwischen {} und {}.\n"
                        "    Übernehme Daten zu Elektrifizierung, Streckenklasse vom letzten Segment"
                        .format(code_start, code_end))
    # Generate a median segment for the route number
    if not last_known_segment and route_number in path_data:
        logging.warning("    Kein letztes Streckensegment bekannt. Verwende Median der Gesamtstrecke")
        last_known_segment = Track(
            route_number=route_number,
            electrified=statistics.median_high((track.electrified for track in path_data[route_number].tracks)),
            length=0,
            kind=statistics.median_high((track.kind for track in path_data[route_number].tracks)),
            from_km=None,
            to_km=None
        )
    return Track(
        route_number=route_number,
        electrified=last_known_segment.electrified if last_known_segment else False,
        length=0,
        kind=last_known_segment.kind if last_known_segment else -1,
        from_km=last_known_segment.to_km if last_known_segment else None,
        to_km=to_km
    )


def convert_waypoints_to_route(waypoints: List[CodeWaypoint],
                               station_data: List[Station],
                               path_data: List[Path]) -> Route:
    codes_to_station = {code: station for code, station in iter_stations_by_codes_reverse(station_data)}
    route_number_to_path = {path.route_numer: path for path in path_data}
    tracks_used = []
    for (waypoint, next_waypoint) in zip(waypoints, waypoints[1:]):
        tracks_between_waypoints = []
        station = codes_to_station[waypoint.code]
        next_station = codes_to_station[next_waypoint.code]
        if waypoint.next_route_number and waypoint.next_route_number in route_number_to_path:
            if station.locations_path and next_station.locations_path:
                path_used: Path = route_number_to_path[waypoint.next_route_number]
                # Now we need to get the segments, i.e. we need to figure out what tracks/segments are used
                station_km = [location.lfd_km for location in station.locations_path
                              if location.route_number == waypoint.next_route_number]
                next_station_km = [location.lfd_km for location in next_station.locations_path
                                   if location.route_number == waypoint.next_route_number]
                if station_km and next_station_km:
                    station_km = station_km[0]
                    next_station_km = next_station_km[0]
                    km_start = min(station_km, next_station_km)
                    km_end = max(station_km, next_station_km)
                    for track in path_used.tracks:
                        if km_start <= track.from_km <= km_end or km_start <= track.to_km <= km_end:
                            tracks_between_waypoints.append(track)
                else:
                    next_station_km = next_station_km[0] if next_station_km else None
                    last_known_segment = tracks_between_waypoints[-1] if tracks_between_waypoints else None
                    tracks_between_waypoints.append(track_from_path(
                        waypoint.next_route_number,
                        last_known_segment,
                        next_station_km,
                        route_number_to_path,
                        code_start=waypoint.code,
                        code_end=next_waypoint.code
                    ))
            else:
                # We only have the route number
                last_known_segment = tracks_between_waypoints[-1] if tracks_between_waypoints else None
                tracks_between_waypoints.append(track_from_path(
                    waypoint.next_route_number,
                    last_known_segment,
                    None,
                    route_number_to_path,
                    code_start=waypoint.code,
                    code_end=next_waypoint.code
                ))
        else:
            tracks_between_waypoints.append(invalid_track(waypoint.next_route_number))
        tracks_used.append(tuple(tracks_between_waypoints))

    return Route(
        waypoints,
        tracks_used
    )
