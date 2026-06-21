import { createHash } from "node:crypto";
import { copyFile, mkdir, writeFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import ffmpegPath from "ffmpeg-static";
import ffprobePackage from "ffprobe-static";

const here = dirname(fileURLToPath(import.meta.url));
const destination = join(here, "..", "src-tauri", "resources", "bin");
const extension = process.platform === "win32" ? ".exe" : "";
const ffprobePath = ffprobePackage.path;

if (!ffmpegPath || !ffprobePath) {
  throw new Error(`No bundled media binaries for ${process.platform}/${process.arch}`);
}

await mkdir(destination, { recursive: true });
const outputs = [
  [ffmpegPath, join(destination, `ffmpeg${extension}`)],
  [ffprobePath, join(destination, `ffprobe${extension}`)],
];

const manifest = {};
for (const [source, target] of outputs) {
  await copyFile(source, target);
  const result = spawnSync(target, ["-version"], { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`${basename(target)} failed its version check: ${result.stderr}`);
  }
  const bytes = await import("node:fs/promises").then(({ readFile }) => readFile(target));
  manifest[basename(target)] = {
    sha256: createHash("sha256").update(bytes).digest("hex"),
    source: basename(source),
    version: result.stdout.split(/\r?\n/, 1)[0],
  };
}

const encoderCheck = spawnSync(outputs[0][1], ["-hide_banner", "-encoders"], { encoding: "utf8" });
const filterCheck = spawnSync(outputs[0][1], ["-hide_banner", "-filters"], { encoding: "utf8" });
for (const required of ["libx264", "libmp3lame", "aac"]) {
  if (!encoderCheck.stdout.includes(required)) {
    throw new Error(`Bundled FFmpeg is missing required encoder: ${required}`);
  }
}
if (!/(^|\s)(ass|subtitles)(\s|$)/m.test(filterCheck.stdout)) {
  throw new Error("Bundled FFmpeg is missing libass subtitle filters");
}

await writeFile(join(destination, "checksums.json"), `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`Prepared FFmpeg resources in ${destination}`);
