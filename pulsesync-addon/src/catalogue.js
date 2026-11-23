export const numberId = /^\d{1,18}$/;

const explicitArtist = /\s+(?:исполнител(?:ь|я|ьница|ьницы)|в исполнении)\s*/iu;
const possibleArtist = /\s+(?:от|by)\s+/giu;
const cyrillic = "абвгдежзийклмнопрстуфхцчшщъыьэюя";
const latin = [
  "a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
  "s", "t", "u", "f", "h", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya",
];
const transliteration = new Map([...cyrillic].map((letter, i) => [letter, latin[i]]));

function normalized(text) {
  return text.toLowerCase().replaceAll("ё", "е").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function parseQuery(query) {
  const text = query.trim();
  const strict = explicitArtist.exec(text);
  const separator = strict ?? [...text.matchAll(possibleArtist)].at(-1);
  if (!separator) return { title: text, artist: "", strict: false };
  return {
    title: text.slice(0, separator.index).trim().replace(/[,;:\-]+$/u, "").trim(),
    artist: text.slice(separator.index + separator[0].length).trim(),
    strict: Boolean(strict),
  };
}

function variants(query, alternatives) {
  return [...new Set([query, ...(Array.isArray(alternatives) ? alternatives.slice(0, 2) : [])])]
    .filter((value) => typeof value === "string" && value.trim() && value.length <= 200);
}

function comparable(text) {
  return [...text].map((letter) => transliteration.get(letter) ?? letter).join("").replaceAll(" ", "");
}

function editDistance(a, b) {
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++) {
      next[j] = Math.min(previous[j] + 1, next[j - 1] + 1, previous[j - 1] + (a[i - 1] !== b[j - 1]));
    }
    previous = next;
  }
  return previous[b.length];
}

function wordMatch(actual, expected) {
  const a = normalized(actual);
  const b = normalized(expected);
  if (!a || !b) return 0;
  if (a === b) return 4;
  const left = comparable(a);
  const right = comparable(b);
  if (!left || !right) return 0;
  if (left === right) return 3;
  // Allow slight spelling differences only across scripts. Two distinct names
  // such as Adele and Adela should never be treated as aliases.
  if (/[а-я]/u.test(a) !== /[а-я]/u.test(b) && Math.min(left.length, right.length) >= 4) {
    const limit = Math.max(1, Math.floor(Math.max(left.length, right.length) / 4));
    if (Math.abs(left.length - right.length) <= limit && editDistance(left, right) <= limit) return 2;
  }
  return 0;
}

function artistMatch(artists, expected) {
  return Math.max(0, ...artists.map((artist) => wordMatch(artist.name ?? "", expected)));
}

async function catalogue(api, query, type) {
  const response = await api.resources.read(
    { resource: "search", method: "getInstantMixedSearch" },
    { text: query, type: [type], filter: type, page: 0, pageSize: 20 },
  );
  return (response?.results ?? [])
    .filter((result) => result.type === type && result[type]?.available !== false
      && numberId.test(String(result[type]?.id)))
    .map((result) => result[type]);
}

function trackChoice(track) {
  const albumId = String(track.albums?.[0]?.id ?? "");
  return { action: "track", trackId: String(track.id), albumId: numberId.test(albumId) ? albumId : undefined };
}

function ordinaryRelevance(track, text) {
  const title = wordMatch(track.title ?? "", text);
  if (title) return title + 2;
  const artists = (track.artists ?? []).map((artist) => artist.name).join(" ");
  const metadata = new Set(normalized(`${track.title ?? ""} ${artists}`).split(" "));
  return text.split(" ").every((word) => metadata.has(word)) ? 2 : 1;
}

export async function searchTrack(api, query, alternatives, artist) {
  const queries = variants(query, alternatives);
  if (!queries.length) throw new Error("Пустой или слишком длинный запрос");
  const hasArtist = typeof artist === "string" && Boolean(artist.trim()) && artist.length <= 200;
  const plans = queries.map((value) => hasArtist ? { title: value, artist, strict: true } : parseQuery(value));
  const strict = plans.some((plan) => plan.strict);
  const qualified = plans.filter((plan) => plan.artist && plan.title && (!strict || plan.strict));
  if (strict && !qualified.length) throw new Error("Не разобрал имя исполнителя");

  const jobs = qualified.length ? qualified : queries.map((title) => ({ title, artist: "" }));
  const results = await Promise.allSettled(jobs.map((plan) => catalogue(
    api, plan.artist ? `${plan.title} ${plan.artist}` : plan.title, "track",
  )));
  if (results.every((result) => result.status === "rejected")) {
    throw new Error("Поиск Яндекс Музыки недоступен");
  }
  let track;
  let best = 0;
  for (const [i, result] of results.entries()) {
    if (result.status !== "fulfilled") continue;
    const plan = jobs[i];
    for (const candidate of result.value) {
      const score = plan.artist
        ? qualifiedRelevance(candidate, plan)
        : ordinaryRelevance(candidate, normalized(plan.title));
      if (score > best) {
        best = score;
        track = candidate;
      }
    }
  }
  // A weak separator can also be part of a song title, e.g. «Stand by Me».
  // In that case the complete name has to match exactly.
  if (!track && qualified.length && !strict) {
    const original = await catalogue(api, query, "track");
    track = original.find((candidate) => normalized(candidate.title ?? "") === normalized(query));
  }
  if (!qualified.length && best <= 2) {
    const songs = await artistTracks(api, query, alternatives, { optional: true });
    if (songs) return songs;
  }
  if (!track) {
    throw new Error(qualified.length
      ? `Не нашёл «${qualified[0].title}» у исполнителя «${qualified[0].artist}»`
      : "Трек не найден");
  }
  return trackChoice(track);
}

function qualifiedRelevance(candidate, plan) {
  const title = wordMatch(candidate.title ?? "", plan.title);
  const artist = artistMatch(candidate.artists ?? [], plan.artist);
  return title && artist ? 5 * artist + title : 0;
}

export async function artistTracks(api, query, alternatives, { optional = false } = {}) {
  const names = variants(query, alternatives);
  if (!names.length) throw new Error("Не разобрал имя исполнителя");
  const results = await Promise.allSettled(names.map((name) => catalogue(api, name, "artist")));
  if (results.every((result) => result.status === "rejected")) {
    if (optional) return null;
    throw new Error("Поиск Яндекс Музыки недоступен");
  }
  let artist;
  let best = optional ? 2 : 0;
  for (const [i, result] of results.entries()) {
    if (result.status !== "fulfilled") continue;
    for (const candidate of result.value) {
      const score = wordMatch(candidate.name ?? "", names[i]);
      if (score > best) {
        best = score;
        artist = candidate;
      }
    }
  }
  if (!artist) {
    if (optional) return null;
    throw new Error(`Не нашёл исполнителя «${names[0]}»`);
  }
  const response = await api.resources.read(
    { resource: "artists", method: "getArtistTracks" },
    { artistId: String(artist.id), page: 0, pageSize: 10 },
  );
  const tracks = [...new Map((response?.tracks ?? [])
    .filter((track) => track.available !== false && numberId.test(String(track.id)))
    .map((track) => [String(track.id), trackChoice(track)])).values()];
  if (!tracks.length) throw new Error(`У исполнителя «${artist.name}» нет доступных треков`);
  return { ...tracks[0], nextTracks: tracks.slice(1), artistName: artist.name };
}
