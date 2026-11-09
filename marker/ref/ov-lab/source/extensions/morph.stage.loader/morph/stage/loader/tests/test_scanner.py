"""스캔 규칙 (순수 파일시스템)."""

import os
import tempfile

import omni.kit.test

from .. import scanner
from ..scanner import SceneInfo


def _touch(path, data=b"#usda 1.0\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class TestScanner(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.warnings = []

    async def tearDown(self):
        self._tmp.cleanup()

    def _scan(self, entries, exts=scanner.DEFAULT_EXTENSIONS, depth=4, max_scenes=1000, samples=None):
        roots = scanner.parse_roots(entries, samples, warn=self.warnings.append)
        return scanner.scan(roots, scanner.normalize_extensions(exts), depth, max_scenes, warn=self.warnings.append)

    async def test_depth_and_extensions(self):
        a = os.path.join(self.root, "a")
        _touch(os.path.join(a, "top.usda"))
        _touch(os.path.join(a, "UPPER.USD"))
        _touch(os.path.join(a, "notes.txt"))
        _touch(os.path.join(a, "d1", "one.usdc"))
        _touch(os.path.join(a, "d1", "d2", "two.usdz"))
        _touch(os.path.join(a, ".hidden", "secret.usda"))

        ids = [s.id for s in self._scan([f"lib={a}"], depth=0)]
        self.assertEqual(ids, ["lib/top.usda", "lib/UPPER.USD"])

        ids = [s.id for s in self._scan([f"lib={a}"], depth=1)]
        self.assertEqual(ids, ["lib/d1/one.usdc", "lib/top.usda", "lib/UPPER.USD"])

        ids = [s.id for s in self._scan([f"lib={a}"], depth=4)]
        self.assertEqual(ids, ["lib/d1/d2/two.usdz", "lib/d1/one.usdc", "lib/top.usda", "lib/UPPER.USD"])
        self.assertNotIn("lib/.hidden/secret.usda", ids)

        ids = [s.id for s in self._scan([f"lib={a}"], exts=["USDA"], depth=4)]
        self.assertEqual(ids, ["lib/top.usda"])

    async def test_scene_info_fields(self):
        a = os.path.join(self.root, "a")
        _touch(os.path.join(a, "sub", "x.usda"), b"#usda 1.0\n(\n)\n")
        (info,) = self._scan([a])
        self.assertIsInstance(info, SceneInfo)
        self.assertEqual(info.id, "a/sub/x.usda")
        self.assertEqual(info.name, "x.usda")
        self.assertEqual(info.root, "a")
        self.assertTrue(os.path.isabs(info.path))
        self.assertEqual(os.path.normcase(info.path), os.path.normcase(os.path.join(a, "sub", "x.usda")))
        self.assertEqual(info.size_bytes, len(b"#usda 1.0\n(\n)\n"))
        self.assertEqual(info.to_dict(), {"id": "a/sub/x.usda", "name": "x.usda", "root": "a", "size_bytes": info.size_bytes})
        # 같은 파일은 다시 스캔해도 같은 id
        (again,) = self._scan([a])
        self.assertEqual(again, info)

    async def test_root_names(self):
        a = os.path.join(self.root, "a")
        b = os.path.join(self.root, "b")
        s = os.path.join(self.root, "smp")
        for d in (a, b, s):
            _touch(os.path.join(d, "s.usda"))
        roots = scanner.parse_roots([f"my lib!={a}", f"my_lib={b}", a, "relative/path", ""], s, warn=self.warnings.append)
        self.assertEqual([n for n, _ in roots], ["my_lib", "my_lib_2", "a", "samples"])
        ids = [x.id for x in scanner.scan(roots, scanner.DEFAULT_EXTENSIONS, 4, 1000)]
        # 루트 순서 = 설정 순서, samples 맨 뒤
        self.assertEqual(ids, ["my_lib/s.usda", "my_lib_2/s.usda", "a/s.usda", "samples/s.usda"])
        self.assertEqual(len(self.warnings), 2)  # 상대 경로, 빈 항목

    async def test_missing_root_and_max_scenes(self):
        a = os.path.join(self.root, "a")
        for i in range(5):
            _touch(os.path.join(a, f"s{i}.usda"))
        missing = os.path.join(self.root, "nope")
        scenes = self._scan([missing, a], max_scenes=3)
        self.assertEqual([s.id for s in scenes], ["a/s0.usda", "a/s1.usda", "a/s2.usda"])
        self.assertTrue(any("nope" in w for w in self.warnings))
        self.assertTrue(any("maxScenes" in w for w in self.warnings))

    async def test_norm_path(self):
        p = os.path.join(self.root, "x.usda")
        self.assertEqual(scanner.path_key(p), scanner.path_key(p.replace("\\", "/")))
        self.assertEqual(scanner.norm_path("omniverse://host/a/b.usd"), "omniverse://host/a/b.usd")
        self.assertIsNone(scanner.norm_path(""))
        self.assertEqual(scanner.display_name("omniverse://host/a/b.usd"), "b.usd")
        self.assertEqual(scanner.display_name(p), "x.usda")
        if os.name == "nt":
            self.assertEqual(scanner.norm_path("file:/C:/a/b.usd"), os.path.normpath("C:/a/b.usd"))
            self.assertEqual(scanner.norm_path("file:///C:/a/b.usd"), os.path.normpath("C:/a/b.usd"))

    async def test_hidden_files_included(self):
        a = os.path.join(self.root, "a")
        _touch(os.path.join(a, ".hidden.usda"))
        _touch(os.path.join(a, "sub", ".also_hidden.usd"))
        _touch(os.path.join(a, ".dir", "skipped.usda"))
        ids = [s.id for s in self._scan([f"lib={a}"])]
        self.assertEqual(ids, ["lib/.hidden.usda", "lib/sub/.also_hidden.usd"])  # 숨김 파일 포함, 숨김 폴더 제외

    async def test_path_with_equals_is_path(self):
        # `=` 앞부분에 구분자·드라이브 콜론이 있으면 항목 전체가 경로 (예: C:\x=y)
        d = os.path.join(self.root, "x=y")
        _touch(os.path.join(d, "s.usda"))
        self.assertTrue(os.path.isabs(d))
        roots = scanner.parse_roots([d], warn=self.warnings.append)
        self.assertEqual(roots, [("x_y", os.path.normpath(d))])
        self.assertEqual([s.id for s in self._scan([d])], ["x_y/s.usda"])
        # 이름=경로 형식에서 경로 쪽에 `=`가 있어도 첫 `=`로만 나눈다
        roots = scanner.parse_roots([f"lib={d}"], warn=self.warnings.append)
        self.assertEqual(roots, [("lib", os.path.normpath(d))])
        self.assertEqual(self.warnings, [])

    async def test_link_cycle(self):
        a = os.path.join(self.root, "a")
        _touch(os.path.join(a, "top.usda"))
        _touch(os.path.join(a, "sub", "x.usda"))
        link = os.path.join(a, "sub", "loop")  # a/sub/loop -> a (순환)
        made = False
        try:
            os.symlink(a, link, target_is_directory=True)
            made = True
        except (OSError, NotImplementedError):
            if os.name == "nt":
                try:
                    import _winapi

                    _winapi.CreateJunction(a, link)
                    made = True
                except OSError:
                    pass
        if not made:
            return  # 링크를 만들 권한이 없으면 건너뜀
        try:
            ids = [s.id for s in self._scan([f"lib={a}"], depth=10)]
            self.assertEqual(ids, ["lib/sub/x.usda", "lib/top.usda"])  # 끝나고, 같은 폴더를 두 번 훑지 않음
        finally:
            try:
                os.rmdir(link)
            except OSError:
                os.unlink(link)
