// 개발 서버 전용: 페이지가 POST /__save?name=<파일명> 으로 보낸 본문을 _out/ 에 저장한다
// (자동 점검 시 영상 프레임 캡처·self test 결과를 디스크로 꺼내기 위함).
import { mkdirSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { defineConfig } from "vite";

const OUT = join(import.meta.dirname, "_out");

export default defineConfig({
  plugins: [
    {
      name: "morph-save",
      configureServer(server) {
        server.middlewares.use("/__save", (req, res) => {
          if (req.method !== "POST") return void (res.statusCode = 405, res.end());
          const name = basename(new URL(req.url, "http://x").searchParams.get("name") || "out.bin");
          const chunks = [];
          req.on("data", (c) => chunks.push(c));
          req.on("end", () => {
            mkdirSync(OUT, { recursive: true });
            writeFileSync(join(OUT, name), Buffer.concat(chunks));
            res.end("ok");
          });
        });
      },
    },
  ],
});
