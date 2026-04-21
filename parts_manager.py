from pxr import Usd, Sdf
import omni.usd


# load_prims 경로: 루트 아래 고정
LOAD_PRIMS_PATH = "/World/load_prims"  # 실제 환경에 따라 /Root/load_prims 로 변경


class PartsManager:

    def get_stage(self) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    def get_load_prim_names(self) -> list[str]:
        """
        load_prims 아래 직계 자식 프림의 이름(name, 경로 아님) 목록을 반환.
        load_prims 프림이 없으면 빈 리스트 반환.
        """
        stage = self.get_stage()
        if stage is None:
            return []

        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []

        names = [child.GetName() for child in load_prims_prim.GetChildren()]
        return names

    def get_load_prim_paths(self) -> list[str]:
        """
        load_prims 아래 직계 자식 프림의 전체 SdfPath(문자열) 목록을 반환.
        """
        stage = self.get_stage()
        if stage is None:
            return []

        load_prims_prim = stage.GetPrimAtPath(LOAD_PRIMS_PATH)
        if not load_prims_prim.IsValid():
            print(f"[PartsManager] '{LOAD_PRIMS_PATH}' not found in stage.")
            return []

        paths = [str(child.GetPath()) for child in load_prims_prim.GetChildren()]
        return paths
