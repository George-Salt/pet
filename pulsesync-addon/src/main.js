import { defineAddon } from "@pulsesync/addon-sdk";

import { artistTracks, numberId, searchTrack } from "./catalogue.js";

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function trackId(snapshot) {
  return snapshot?.track?.id?.toString().split(":")[0];
}

function waveIsActive(state) {
  const context = state?.currentContext?.value;
  return context?.data?.type === "vibe" || context?.contextData?.type === "vibe";
}

async function favoritePlaylist(api) {
  const login = await api.user.getLogin();
  if (typeof login !== "string" || !login.trim()) throw new Error("Войди в аккаунт Яндекс Музыки");
  const page = await api.page.getSnapshot();
  const playlist = page?.type === "playlist" ? page.entity : null;
  const owner = playlist?.owner;
  if (playlist?.kind === 3 && owner?.login === login && playlist.uid && playlist.playlistUuid) {
    const liked = { id: `${playlist.uid}:3`, uuid: playlist.playlistUuid };
    await api.storage.set("likedPlaylist", { login, ...liked });
    return liked;
  }
  const remembered = await api.storage.get("likedPlaylist");
  if (remembered?.login === login && remembered.id && remembered.uuid) {
    return { id: remembered.id, uuid: remembered.uuid };
  }
  return { id: `${login}:3` };
}

function playlistMatches(state, playlist) {
  const value = state?.currentContext?.value;
  return [value?.data, value?.contextData].some((context) => context?.type === "playlist" &&
    (String(context.meta?.id) === playlist.id || playlist.uuid &&
      (context.meta?.uuid === playlist.uuid || context.meta?.playlistUuid === playlist.uuid)));
}

function playbackPosition(snapshot) {
  return typeof snapshot?.progress === "number" ? snapshot.progress : snapshot?.progress?.position;
}

async function confirmedPlayback(api, command) {
  const client = api.client;
  if (command.action === "search") command = await searchTrack(api, command.query, command.alternatives, command.artist);
  if (command.action === "artist") command = await artistTracks(api, command.query, command.alternatives);
  if (command.action === "favorites") command = { action: "playlist", ...(await favoritePlaylist(api)) };
  const previous = await client.getPlayerSnapshot();
  if (command.action === "track" && numberId.test(command.trackId)) {
    await client.playTrackById(command.trackId, { albumId: command.albumId ?? undefined });
  } else if (command.action === "wave") {
    await client.playVibe({ screen: "landing" });
  } else if (command.action === "playlist") {
    await client.playPlaylistById(command.id, { uuid: command.uuid });
  } else {
    throw new Error("Неизвестная команда Pet");
  }

  const deadline = Date.now() + 8_000;
  while (!api.signal.aborted && Date.now() < deadline) {
    const current = await client.getPlayerSnapshot();
    if (current?.isPlaying) {
      if (command.action === "track" && trackId(current) === command.trackId) {
        if (command.nextTracks?.length) {
          for (const next of [...command.nextTracks].reverse()) {
            await client.addTrackToQueueById(next.trackId, { albumId: next.albumId, position: "next" });
          }
        }
        return { title: current.track?.title ?? "", artist: command.artistName ?? "" };
      }
      if (command.action === "wave") {
        const state = await client.getState();
        if (waveIsActive(state) || trackId(current) !== trackId(previous)) {
          return { title: current.track?.title ?? "" };
        }
      }
      if (command.action === "playlist") {
        const restarted = playbackPosition(current) < playbackPosition(previous) - 1;
        if (playlistMatches(await client.getState(), command) ||
            command.uuid && (trackId(current) !== trackId(previous) || restarted)) {
          return { title: current.track?.title ?? "", playlist: "Мне нравится" };
        }
      }
    }
    await pause(230);
  }
  throw new Error(command.action === "playlist" && !command.uuid
    ? "Открой «Мне нравится» в Яндекс Музыке один раз, чтобы Pet запомнил плейлист"
    : "Плеер не подтвердил воспроизведение");
}

export default defineAddon({
  id: "hyprpet-music",
  start(api) {
    const options = {
      method: "POST",
      headers: { Authorization: `Bearer ${HYPRPET_TOKEN}`, "Content-Type": "application/json" },
      signal: api.signal,
      timeoutMs: 2500,
    };

    async function tick() {
      let command;
      try {
        command = await api.net.json(`${HYPRPET_ENDPOINT}poll`, { ...options, body: "{}" });
        if (typeof command?.requestId !== "string") return;
        let result;
        try {
          result = { ok: true, ...(await confirmedPlayback(api, command)) };
        } catch (error) {
          result = { ok: false, error: error instanceof Error ? error.message : "Ошибка плеера" };
        }
        await api.net.json(`${HYPRPET_ENDPOINT}result`, {
          ...options,
          body: JSON.stringify({ ...result, requestId: command.requestId }),
        });
      } catch {
        // Pet opens the local endpoint only while a command is waiting.
      } finally {
        if (!api.signal.aborted) api.setTimeout(tick, 1200);
      }
    }

    void tick();
  },
});
