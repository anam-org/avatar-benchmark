"""Dataset assembly for iframe stimuli.

A Mabyduck dataset is a directory of group folders, each holding the
wrapper JS for one condition plus a config.json. Nothing here knows which
study is being built: the wrapper reports session outcomes back over
postMessage and stamps a per-presentation id onto the rating, which every
study needs identically.

Study-specific folder layout lives with the study.
"""

from __future__ import annotations

import shutil
from pathlib import Path



def build_iframe_js(src: str) -> str:
    """The stimulus wrapper Mabyduck runs for one condition.

    The wrapper mints a presentation id, hands it to the page as `pid`,
    and writes it onto the rating with setMeta().

    The id is a fresh UUID per presentation, generated in the browser.

    The iframe also reports session outcomes back over postMessage.
    """
    origin = src.split("/", 3)[:3]
    origin = "/".join(origin)
    return (
        'const container = document.getElementById("content");\n'
        "\n"
        "const pid = (crypto.randomUUID && crypto.randomUUID()) ||\n"
        "    String(Date.now()) + Math.random().toString(16).slice(2);\n"
        "const sessions = [];\n"
        "\n"
        "const record = () => {\n"
        '    if (typeof setMeta !== "function") return;\n'
        "    try {\n"
        "        setMeta({ pid: pid, sessions: sessions.slice(-4) });\n"
        "    } catch (err) {\n"
        '        console.error("setMeta failed", err);\n'
        "    }\n"
        "};\n"
        "\n"
        "window.addEventListener('message', (ev) => {\n"
        f'    if (ev.origin !== "{origin}") return;\n'
        '    const d = ev.data;\n'
        '    if (!d || d.source !== "anam-benchmark") return;\n'
        '    if (d.type === "session-started") {\n'
        "        sessions.push({ u: d.sessionUuid, p: d.provider });\n"
        '    } else if (d.type === "session-ended") {\n'
        "        const s = sessions.find((x) => x.u === d.sessionUuid);\n"
        "        if (s) {\n"
        "            s.e = d.endState;\n"
        "            s.c = d.connectMs;\n"
        "        }\n"
        "    }\n"
        "    record();\n"
        "});\n"
        "\n"
        "if (container) {\n"
        "    record();\n"
        '    container.style.height = "100%";\n'
        "    container.innerHTML = `\n"
        "        <iframe\n"
        f'            src="{src}&pid=${{pid}}"\n'
        '            allow="camera; microphone; autoplay"\n'
        '            style="width:100%;height:100%;border:none;display:block;background:#000;"\n'
        "        ></iframe>\n"
        "    `;\n"
        "} else {\n"
        '    console.error("Container element not found.");\n'
        "}\n"
    )



def zip_directory(out_root: Path) -> Path:
    archive = shutil.make_archive(
        base_name=str(out_root.with_suffix("")),
        format="zip",
        root_dir=str(out_root),
    )
    return Path(archive)
