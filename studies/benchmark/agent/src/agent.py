"""LiveKit agent for the avatar benchmark.

The web harness embeds {provider, slot, topic, name, mode, sessionUuid} in
the job metadata (RoomAgentDispatch), and this worker attaches the matching
avatar plugin.

Run (dev):  cd agent && uv run python src/agent.py dev
Required env: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET,
DEEPGRAM_API_KEY, GROQ_API_KEY, CARTESIA_API_KEY, plus the avatar
provider keys: ANAM_API_KEY, TAVUS_API_KEY, LIVEAVATAR_API_KEY,
LEMONSLICE_API_KEY, SYNTHESIA_API_KEY. Tavus additionally needs echo-mode
personas — see setup_tavus_personas.py; their ids live in
config/livekit_tavus.json.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import (anam, cartesia, deepgram, lemonslice, liveavatar, openai,
                             silero, synthesia, tavus)

from prompt import build_instructions

load_dotenv()
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_LOCAL = Path(__file__).resolve().parent.parent / "config"
_ROOT = (
    Path(__file__).resolve().parent.parent
    if _LOCAL.exists()
    else Path(__file__).resolve().parent.parent.parent
)
_CFG = json.loads((_ROOT / "config" / "livekit.json").read_text())
_PROVIDERS_CFG = json.loads((_ROOT / "config" / "providers.json").read_text())
_AVATARS = json.loads((_ROOT / "config" / "avatars.json").read_text())
_TAVUS_PERSONAS_PATH = _ROOT / "config" / "livekit_tavus.json"

FEMALE_SLOTS = {"f2", "f3"}

# Arms declared in config/providers.json. An Anam arm pins an avatar model and
# rates the base arm's cast. A Tavus arm carries its own cast under its own key
# in config/avatars.json: Tavus does not serve one replica on two models.
ANAM_ARMS: dict[str, str] = {
    key: cfg["avatarModel"]
    for key, cfg in _PROVIDERS_CFG["anam"].get("arms", {}).items()
}
TAVUS_ARMS: frozenset[str] = frozenset(_PROVIDERS_CFG["tavus"].get("arms", {}))

# Seconds to wait after the avatar joins before opening the conversation.
# A byte stream published before the receiver has registered its audio
# handler is dropped, and the join gates key on the video track. Synthesia
# registers its audio handler after publishing video, so without this wait
# its opening turn is lost. Measured on this cast: 2s loses it in about half
# of sessions, 3s holds. Paid once per session, on the opening turn alone.
# A provider absent from this map waits not at all.
SETTLE_SECONDS: dict[str, float] = {
    "synthesia": float(os.environ.get("SYNTHESIA_SETTLE_SECONDS", "3")),
}

server = AgentServer()


def _slots_for(arm: str) -> dict:
    """Slot map for an arm. Anam arms rate the base arm's cast; every other
    key has its own entry in config/avatars.json."""
    key = "anam" if arm in ANAM_ARMS else arm
    slots = _AVATARS["providers"].get(key)
    if not slots:
        raise ValueError(
            f"no cast for {key!r} in config/avatars.json"
        )
    return slots


def _avatar_for(arm: str, slot: str, name: str):
    slots = _slots_for(arm)
    if slot not in slots:
        raise ValueError(f"unknown slot {slot!r} for arm {arm!r}")
    ids = slots[slot]
    if arm == "anam" or arm in ANAM_ARMS:
        return anam.AvatarSession(
            persona_config=anam.PersonaConfig(
                name=name,
                avatarId=ids["avatarId"],
                avatarModel=ANAM_ARMS.get(arm) or _PROVIDERS_CFG["anam"]["model"],
            ),
        )
    if arm == "tavus" or arm in TAVUS_ARMS:
        personas = json.loads(_TAVUS_PERSONAS_PATH.read_text())
        replica = ids["replicaId"]
        if replica not in personas:
            raise ValueError(
                f"no echo-mode persona for replica {replica} ({arm}/{slot}); "
                f"run agent/setup_tavus_personas.py to create one"
            )
        return tavus.AvatarSession(
            replica_id=replica,
            persona_id=personas[replica],
        )
    if arm == "heygen":
        return liveavatar.AvatarSession(avatar_id=ids["avatarId"])
    if arm == "synthesia":
        # Face only: the avatar joins the room and renders our pipeline's
        # audio. One gallery id per slot.
        return synthesia.AvatarSession(
            avatar_config=synthesia.AvatarConfig(avatar_ids=[ids["avatarId"]]),
            # 60s, as Synthesia's reference agent uses.
            join_timeout=60.0,
        )
    if arm == "lemonslice":
        return lemonslice.AvatarSession(
            agent_image_url=ids["imageUrl"],
            idle_timeout=-1,  # session length is governed by the harness cap
        )
    raise ValueError(f"unknown arm {arm!r}")


@server.rtc_session(agent_name=_CFG["agentName"])
async def benchmark_agent(ctx: agents.JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    provider = meta["provider"]
    slot = meta["slot"]
    topic = meta.get("topic", "")
    name = meta.get("name", "Avatar")
    mode = meta.get("mode", "main")

    pipe = _CFG["pipeline"]
    # Voice comes from the dispatch metadata, sampled per session by the
    # worker: the first 32 bits of sha256(sessionUuid) mod the pool size.
    # Fallback: recompute the same hash locally (direct dispatches, e.g.
    # smoke tests).
    voice = meta.get("voiceId")
    if not voice:
        pool = (
            pipe["tts"]["voicePoolFemale"]
            if slot in FEMALE_SLOTS
            else pipe["tts"]["voicePoolMale"]
        )
        digest = hashlib.sha256(meta.get("sessionUuid", "").encode()).digest()
        voice = pool[int.from_bytes(digest[:4], "big") % len(pool)]["id"]

    session = AgentSession(
        # mode and min_words come from config/livekit.json.
        turn_handling={"interruption": {
            "mode": pipe["interruption"]["mode"],
            "min_words": pipe["interruption"]["min_words"],
        }},
        stt=deepgram.STT(model=pipe["stt"]["model"]),
        llm=openai.LLM(
            model=pipe["llm"]["model"],
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ["GROQ_API_KEY"],
        ),
        tts=cartesia.TTS(model=pipe["tts"]["model"], voice=voice),
        vad=silero.VAD.load(),
    )

    avatar = _avatar_for(provider, slot, name)
    # The model an Anam arm pins, empty for every other arm. The mint route
    # sends the value the signed stimulus authorised; a direct dispatch (a smoke
    # test) sends none and the arm's own declaration stands in. Either way it
    # must match what the arm declares, so a session cannot run a model the
    # study never declared, or run the default while claiming an arm.
    expected = ANAM_ARMS.get(provider, "")
    model = meta.get("avatarModel")
    if model is None:
        model = expected
    if model != expected:
        raise ValueError(
            f"arm {provider!r} declares model {expected or '(default)'} "
            f"but was sent {model or '(default)'}"
        )

    await avatar.start(session, room=ctx.room)

    # Documented lifecycle (docs.livekit.io/agents/models/avatar/): wait for
    # the avatar worker to join and publish BEFORE the session starts. On
    # timeout this raises and the session fails loudly.
    await avatar.wait_for_join()
    logging.info("avatar joined", extra={"provider": provider, "slot": slot})

    await session.start(
        agent=Agent(instructions=build_instructions(name, topic, mode)),
        room=ctx.room,
    )
    settle = SETTLE_SECONDS.get(provider, 0.0)
    if settle > 0:
        logging.info("settling before the opening turn",
                     extra={"provider": provider, "seconds": settle})
        await asyncio.sleep(settle)
    logging.info("session started; generating opening turn")
    # The LLM opens the conversation itself by following [INITIATION] in the
    # ported turnkey prompt (main: greeting then Question 1; practice:
    # training intro, then wait for readiness).
    await session.generate_reply()
    logging.info("opening turn played")


if __name__ == "__main__":
    agents.cli.run_app(server)
