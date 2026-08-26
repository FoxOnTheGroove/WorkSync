from .ebs_simulate import EbsSimulate, FACES, GRID

__all__ = ["EbsSimulateService", "FACES", "GRID"]


class EbsSimulateService:
    """Public API for the EBS simulation. The implementation is reached only through here."""

    _simulate = None

    @classmethod
    def initialize(cls):
        cls._simulate = EbsSimulate()

    @classmethod
    def finalize(cls):
        if cls._simulate:
            cls._simulate.teardown()
        cls._simulate = None

    @classmethod
    def set_xml_path(cls, path):
        return cls._simulate.set_xml_path(path)

    @classmethod
    def set_ebs_paths(cls, path_2port, path_3port):
        return cls._simulate.set_ebs_paths(path_2port, path_3port)

    @classmethod
    def set_clearance(cls, value):
        return cls._simulate.set_clearance(value)

    @classmethod
    def set_search_root(cls, path):
        return cls._simulate.set_search_root(path)

    @classmethod
    def set_precision(cls, mode):
        return cls._simulate.set_precision(mode)

    @classmethod
    def set_rail_root(cls, path):
        return cls._simulate.set_rail_root(path)

    @classmethod
    def load_ports(cls):
        return cls._simulate.load_ports()

    @classmethod
    def init(cls):
        return cls._simulate.init()

    @classmethod
    def build_index(cls):
        return cls._simulate.build_index()

    @classmethod
    def get_selected_equipment(cls):
        return cls._simulate.get_selected_equipment()

    @classmethod
    def get_port_count(cls, eqp_id):
        return cls._simulate.get_port_count(eqp_id)

    @classmethod
    def prepare(cls, equipment=""):
        return cls._simulate.prepare(equipment)

    @classmethod
    def focus(cls):
        return cls._simulate.focus()

    @classmethod
    def align(cls):
        return cls._simulate.align()

    @classmethod
    def collide(cls):
        return cls._simulate.collide()

    @classmethod
    def simulate(cls, equipment=""):
        return cls._simulate.simulate(equipment)

    @classmethod
    def clear_markers(cls):
        return cls._simulate.clear_markers()

    @classmethod
    def get_result(cls):
        return cls._simulate.get_result()

    @classmethod
    def get_grid_shape(cls):
        return cls._simulate.get_grid_shape()

    @classmethod
    def get_notes(cls):
        return cls._simulate.get_notes()

    @classmethod
    def get_timings(cls):
        return cls._simulate.get_timings()
