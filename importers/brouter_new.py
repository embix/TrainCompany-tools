from __future__ import annotations

import itertools
import logging
import re
from functools import lru_cache
from typing import List, Optional, Tuple, Dict, Set, Any, Iterator

import geopy.distance
import gpxpy
import rdp
import unidecode
from geopy.extra.rate_limiter import RateLimiter
from gpxpy.gpx import GPXTrackPoint

import geo
from geo import Location
from geo.photon_advanced_reverse import PhotonAdvancedReverse
from importer import Importer
from structures.country import countries
from structures.route import CodeWaypoint
from structures.station import Station, CodeTuple
import numpy as np


class BrouterImporterNew(Importer[CodeWaypoint]):
    stations: List[Station]
    name_to_station: Dict[str, Station]
    language: str | bool
    fallback_town: bool

    def __init__(self, station_data: List[Station],
                 language: str | bool = False,
                 fallback_town: bool = False):
        self.stations = station_data
        self.name_to_station = {normalize_name(station.name): station
                                for station in station_data}
        self.language = language
        self.fallback_town = fallback_town

    def import_data(self, file_name: str) -> List[CodeWaypoint]:
        with open(file_name, encoding='utf-8') as input_file:
            gpx = gpxpy.parse(input_file)

        # Step 1: Find the OSM railway stations for all waypoints
        geolocator = PhotonAdvancedReverse()
        reverse = RateLimiter(geolocator.reverse, min_delay_seconds=0.5, max_retries=3)

        # It may be possible that the waypoint has a different location to its station
        waypoint_location_to_station_location = {}

        for waypoint in gpx.waypoints:
            # Find stations close to the given waypoint location
            possible_stations: List[geopy.location.Location] | None = reverse(
                geopy.Point(latitude=waypoint.latitude, longitude=waypoint.longitude),
                exactly_one=False,
                limit=6,
                query_string_filter='+'.join(["osm_value:stop", "osm_value:station", "osm_value:halt"]),
                language=self.language,
                timeout=10
            )
            if possible_stations is None:
                if self.fallback_town:
                    logging_fn = logging.info
                else:
                    logging_fn = logging.error

                logging_fn(f"No station found for location (lat={waypoint.latitude}, lon={waypoint.longitude})")
                logging.debug("On G/M: https://maps.google.com/maps/@{},{},17z/data=!3m1!1e3".format(
                    waypoint.latitude,
                    waypoint.longitude
                ))
                logging.debug("On OSM: https://openstreetmap.org/#map=17/{}/{}&layers=T".format(
                    waypoint.latitude,
                    waypoint.longitude
                ))

                if self.fallback_town:
                    # Now we will try to look for a nearby town/city/village instead
                    possible_stations = reverse(
                        geopy.Point(latitude=waypoint.latitude, longitude=waypoint.longitude),
                        exactly_one=False,
                        limit=6,
                        query_string_filter='+'.join(["osm_value:city", "osm_value:town", "osm_value:borough",
                                                      "osm_value:hamlet", "osm_value:village",
                                                      "osm_value:municipality"]),
                        language=self.language,
                        timeout=10
                    )
                    if possible_stations is None:
                        logging.error(
                            f"No station or town found for location (lat={waypoint.latitude}, lon={waypoint.longitude})")
                        logging.debug("On G/M: https://maps.google.com/maps/@{},{},17z/data=!3m1!1e3".format(
                            waypoint.latitude,
                            waypoint.longitude
                        ))
                        logging.debug("On OSM: https://openstreetmap.org/#map=17/{}/{}&layers=T".format(
                            waypoint.latitude,
                            waypoint.longitude
                        ))
                        logging.error("Ignoring station")
                        continue
                else:
                    logging.error("Ignoring station")
                    continue

            for possible_station in possible_stations:
                if 'name' not in possible_station.raw['properties']:
                    logging.info("Station ohne Namen: {}".format(possible_station.raw))

            possible_station_names = (normalize_name(station.raw['properties']['name'])
                                      for station in possible_stations if 'name' in station.raw['properties'])
            possible_station_groups = [group_from_photon_response(station.raw['properties']) for station in
                                       possible_stations]
            # Is one of these names in our data set?
            for name in possible_station_names:
                if name in self.name_to_station:
                    # Remove it from the lookup table to prevent having the same station twice
                    station = self.name_to_station.pop(name)
                    # Add this location if necessary
                    if station.location is None:
                        station_dict = station.__dict__
                        station_dict.pop('location')
                        station = Station(
                            **station_dict,
                            location=Location(
                                latitude=waypoint.latitude,
                                longitude=waypoint.longitude
                            )
                        )
                        self.stations.append(station)
                    break
            else:
                logging.info("Couldn't find any of these stations: {}. Creating new one.".format(
                    [station.raw['properties']['name'] for station in possible_stations if
                     'name' in station.raw['properties']]
                ))
                # We create a new station because we can't find an existing one
                new_station = possible_stations[0]
                new_station_properties = possible_stations[0].raw['properties']

                country = countries[new_station_properties['countrycode']]
                # The new codes use the osm_id - Flag + O (for OpenStreetMaps) + osm_id
                code = f"{country.flag}O{new_station_properties['osm_id']}"
                # It might be longer than the limit...
                assert len(bytes(code, "utf-8")) <= 20

                # Assemble the station with all data we have
                station = Station(
                    name=new_station_properties['name'],
                    codes=CodeTuple(code),
                    # 69 is not a UIC country code
                    number=int("69" + str(new_station_properties['osm_id'])),
                    location=geo.Location(
                        latitude=new_station.latitude,
                        longitude=new_station.longitude
                    ),
                    _group=largest_group(possible_station_groups)
                )

                logging.debug(f"New station: {station}")

                # Add to the data set (it should propagate to the original data set as well)
                self.stations.append(station)
                # We do not want to add it to the lookup table to ensure that we only have unique stations
            waypoint_location_to_station_location[Location(longitude=waypoint.longitude,
                                                           latitude=waypoint.latitude)] = station

        # Now we go through the file, accumulate distances and create the new waypoints
        distance_total = 0.0
        code_waypoints = []
        track_paths: List[Tuple[List[GPXTrackPoint], Station]] = []
        last_location: Optional[Location] = None
        last_stop_index: int = 0
        points = gpx.tracks[0].segments[0].points
        for index, trackpoint in enumerate(gpx.tracks[0].segments[0].points):
            location = Location(
                latitude=trackpoint.latitude,
                longitude=trackpoint.longitude
            )
            if last_location:
                distance_total += location.distance_float(last_location)
            # Check if we have a stop here
            for waypoint_location, stop in waypoint_location_to_station_location.items():
                if waypoint_location.distance_float(location) < 0.08:
                    # We have a stop here - add the CodeWaypoint
                    code_waypoints.append(CodeWaypoint(
                        code=stop.codes[0],
                        distance_from_start=distance_total,
                        is_stop=True,
                        next_route_number=0
                    ))
                    track_paths.append((points[last_stop_index:index], stop))
                    last_stop_index = index
                    # We don't want to match the stop multiple times
                    waypoint_location_to_station_location.pop(waypoint_location)
                    break
            last_location = location
        return code_waypoints


def simplify_path_with_stops(path: List[Tuple[List[GPXTrackPoint], Station]], max_radius: float):
    for index, (segment, station) in enumerate(path):
        path[index] = (list(douglas_peucker(segment, max_radius)), station)


# Based on https://towardsdatascience.com/simplify-polylines-with-the-douglas-peucker-algorithm-ac8ed487a4a1
def douglas_peucker(points: List[GPXTrackPoint], max_radius: float) -> Iterator[GPXTrackPoint]:
    points_array = np.ndarray((len(points), 2), dtype=float)
    for index, point in enumerate(points):
        # We use lat, lon here, because geopy.distance.geodesic uses this format.
        # For the geometric stuff it doesn't matter, as we only care about distances and not directions, etc.
        points_array[index] = (point.latitude, point.longitude)
    selected_points = rdp.rdp(points_array, dist=approximate_distance_to_line, epsilon=max_radius, return_mask=True)
    return itertools.compress(points, selected_points)


def approximate_distance_to_line(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    # NOTE: We assume here that distances are roughly linear to the longitude and latitude difference
    # Step 1: Get the "scale" - i.e., distance_km / distance_lon_lat.
    km_per_lon_lat = geopy.distance.geodesic(start, end).km / np.linalg.norm(start - end)
    distance_lon_lat = rdp.pldist(point, start, end)
    return distance_lon_lat * km_per_lon_lat


delimiters = re.compile(r"[- _]", flags=re.IGNORECASE)
omitted_tokens = re.compile(r"[.']", flags=re.IGNORECASE)
more_than_one_space = re.compile(r"\s\s+")


@lru_cache
def normalize_name(name: str) -> str:
    name = name.lower()
    name = unidecode.unidecode(name)
    name = delimiters.sub(" ", name)
    name = omitted_tokens.sub("", name)
    name = more_than_one_space.sub(" ", name)
    name = name.replace("saint", "st")
    return name


def group_from_photon_response(response: Dict[str, Any]) -> int | None:
    value = response['osm_value']
    if value == 'station':
        group = 2
    elif value == 'stop':
        group = 5
    elif value == 'halt':
        group = 5
    elif value == 'junction':
        group = 4
    else:
        group = None
    return group


def largest_group(groups: List[int]) -> int | None:
    if 0 in groups:
        return 0
    if 1 in groups:
        return 1
    if 2 in groups:
        return 2
    if 5 in groups:
        return 5
    if 3 in groups:
        return 3
    if 6 in groups:
        return 6
    if 4 in groups:
        return 4
    else:
        return None
