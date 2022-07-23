from __future__ import annotations

import argparse
import os
from os import PathLike
from typing import Any, Dict

from tc_utils import TcFile


def cleanup(tc_directory: PathLike | str) -> TcFile:
    path_json = TcFile('Path', tc_directory)
    for path in path_json.data:
        remove_annotations_from_path(path)
    return path_json


def remove_annotations_from_path(path: Dict[str, Any]):
    path.pop('start_long', '')
    path.pop('end_long', '')
    if 'objects' in path:
        for sub_path in path['objects']:
            remove_annotations_from_path(sub_path)


if __name__ == '__main__':
    script_path = os.path.realpath(__file__)
    script_dir = os.path.dirname(script_path)

    parser = argparse.ArgumentParser(description='Entferne nicht mehr benötigte Daten')
    parser.add_argument('--tc-dir', dest='tc_directory', metavar='VERZEICHNIS', type=str,
                        default=os.path.dirname(script_dir),
                        help="Das Verzeichnis, in dem sich die TrainCompany-Daten befinden")
    args = parser.parse_args()

    path_json = cleanup(tc_directory=args.tc_directory)
    path_json.save()