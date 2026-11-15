"""Spherical archive/viewport intersections; no flat RA bounding boxes."""
import numpy as np
from spherical_geometry.polygon import SphericalPolygon
from archive_client import DataError

def polygon(points, center):
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise DataError("A finite celestial polygon with at least three vertices is required.", 422)
    if np.any((points[:,0] < 0) | (points[:,0] >= 360) | (np.abs(points[:,1]) > 90)):
        raise DataError("Polygon coordinates must be ICRS degrees.", 422)
    try:
        result = SphericalPolygon.from_radec(points[:,0], points[:,1], center=tuple(center))
        if not 1e-14 < result.area() < 2*np.pi:
            raise ValueError("degenerate or hemisphere-spanning polygon")
        return result
    except Exception as exc:
        raise DataError(f"Unsupported spherical boundary: {exc}. Use a smaller region.", 422) from exc

def footprint(record):
    row = record.get("sia", record)
    parts = str(row.get("s_region", "")).split()
    if parts[:2] != ["POLYGON", "ICRS"]:
        raise DataError("The observation has no supported authoritative POLYGON ICRS footprint.",422)
    try:
        return polygon(np.asarray(parts[2:], dtype=float).reshape(-1,2), (row["s_ra"], row["s_dec"]))
    except (ValueError, KeyError) as exc:
        raise DataError("Invalid archive footprint metadata.",502) from exc

def _radec(vector):
    v = np.asarray(vector, dtype=float)
    v = v / np.linalg.norm(v)
    return float(np.degrees(np.arctan2(v[1],v[0])) % 360), float(np.degrees(np.arcsin(np.clip(v[2],-1,1))))

def retrieval_target(record, preferred, viewport):
    """Prefer explicit position, then viewport center, then an intersection interior."""
    exposure = footprint(record)
    view = polygon(viewport["polygon"], (viewport["ra"], viewport["dec"]))
    overlap = exposure.intersection(view)
    if overlap.area() <= 1e-14:
        raise DataError("The selected observation does not intersect the active viewport.",404)
    candidates = [(tuple(preferred), "preferred_position"), ((viewport["ra"], viewport["dec"]), "viewport_center")]
    for component in overlap.polygons:
        # The library's interior vector avoids RA wrap and polar arithmetic.
        candidates.append((_radec(component.inside), "spherical_intersection_interior"))
        candidates.append((_radec(np.mean(component.points[:-1],axis=0)), "spherical_intersection_centroid"))
    for position, method in candidates:
        if exposure.contains_radec(*position) and view.contains_radec(*position) and overlap.contains_radec(*position):
            return position, {"method":method,"preferred_position":list(preferred),"viewport":viewport,
                              "intersection_area_sr":float(overlap.area()),"target_in_intersection":True}
    raise DataError("No reliable interior position could be established for the intersection.",422)

def verify_cutout_region(validation, position, viewport):
    view = polygon(viewport["polygon"], (viewport["ra"], viewport["dec"]))
    cutout = polygon(validation["footprint_icrs_deg"], position)
    if not view.contains_radec(*position) or not cutout.intersects_poly(view):
        raise DataError("Retrieved FITS does not overlap the requested viewport.")
    return True
