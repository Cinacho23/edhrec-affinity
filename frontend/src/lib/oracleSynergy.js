// Oracle clues distinguish what creates an event from what benefits from it.
// The deck constructor uses these clues as preferences alongside the learned ranker.
const TOPICS = [
  ["token", "token creation", /\bcreat(?:e|es|ed|ing)\b[^.]{0,125}\btokens?\b|\btokens?\b[^.]{0,45}\b(?:is|are|would be) created\b/, /\bcreat(?:e|es)\b[^.]{0,125}\btokens?\b/],
  ["creature-token", "creature tokens", /\bcreature tokens?\b/, /\bcreat(?:e|es)\b[^.]{0,125}\bcreature tokens?\b/],
  ["treasure", "Treasure tokens", /\btreasures?\b/, /\bcreat(?:e|es)\b[^.]{0,75}\btreasure tokens?\b/],
  ["food", "Food tokens", /\bfood\b/, /\bcreat(?:e|es)\b[^.]{0,75}\bfood tokens?\b/],
  ["clue", "Clue tokens", /\bclues?\b|\binvestigate\b/, /\binvestigate\b|\bcreat(?:e|es)\b[^.]{0,75}\bclue tokens?\b/],
  ["blood", "Blood tokens", /\bblood tokens?\b/, /\bcreat(?:e|es)\b[^.]{0,75}\bblood tokens?\b/],
  ["sacrifice", "sacrificing permanents", /\bsacrific(?:e|es|ed)\b/, /\bsacrifice (?:an? |one |another |any number of |two )?(?:other )?(?:creature|artifact|enchantment|permanent|token|land)\b/],
  ["token-sacrifice", "sacrificing tokens", /\btokens?\b[^.]{0,35}\bsacrificed\b|\bsacrifice\b[^.]{0,24}\btokens?\b/, /\bsacrifice (?:an? |one |another |any number of |two )?(?:other )?(?:creatures?|artifacts?|enchantments?|permanents?|tokens?)\b/],
  ["dies", "creatures dying", /\b(?:creatures?|tokens?)\b[^.]{0,60}\b(?:dies?|dying)\b|\bthis card dies\b/, /\bsacrifice (?:an? |one |another |any number of |two )?(?:other )?(?:creatures?|tokens?)\b/],
  ["artifact-graveyard", "artifacts going to the graveyard", /\bartifacts?\b[^.]{0,75}\b(?:is|are) put into (?:a|your) graveyard\b/, /\bsacrifice (?:an? |one |another )?(?:other )?artifacts?\b/],
  ["attack", "attacking", /\b(?:you attack|attacks?|attacking creatures?)\b/, /\badditional combat phase\b|\bcreat(?:e|es)\b[^.]{0,90}\b(?:token|tokens)\b[^.]{0,20}\battacking\b/],
  ["evasion", "connecting in combat", /\b(?:deals?|dealt) combat damage\b/, /\bcan't be blocked\b|\b(?:gain|has|have) (?:flying|menace|trample)\b|\badditional combat phase\b/],
  ["landfall", "landfall", /\blandfall\b|\b(?:a land|lands?)\b[^.]{0,50}\benters?\b/, /\b(?:play an? additional land|search your library for)\b[^.]{0,100}\bland cards?\b[^.]{0,50}\b(?:onto the battlefield|into play)\b|\bplay an? additional land\b|\bput\b[^.]{0,50}\bland cards?\b[^.]{0,60}\bonto the battlefield\b/],
  ["life-gain", "life gain", /\b(?:you gain|gained|gain) (?:life|\d+ life)\b/, /\b(?:you )?gain(?:s)? (?:\d+|x|that much|that much plus \d+|life equal to)\b[^.]{0,24}\blife\b|\blifelink\b/],
  ["opponent-life-loss", "opponents losing life", /\bopponents?\b[^.]{0,32}\blos(?:e|es|t) life\b/, /\b(?:target|each|an) opponent loses?\b[^.]{0,24}\blife\b|\bdeals?\b[^.]{0,35}\bdamage to (?:each opponent|target player|any target)\b/],
  ["damage", "noncombat damage", /\bnoncombat damage\b|\b(?:you|an? (?:instant|sorcery)[^.]{0,24}) deals? damage\b/, /\bdeals?\b[^.]{0,35}\bdamage to\b/],
  ["draw", "drawing cards", /\b(?:you draw|second card you draw|cards? you've drawn|cards? drawn)\b/, /\bdraw(?:s)? (?:an? |one |two |three |four |\d+ |that many |x )?cards?\b/],
  ["discard", "discarding cards", /\b(?:you|a player) discard(?:s|ed)?\b|\bcards? you discard\b/, /\bdiscard (?:an? |one |two |your hand|\d+ |that many |x )?(?:cards?\b|your hand\b)/],
  ["opponent-discard", "opponents discarding", /\bopponents?\b[^.]{0,28}\bdiscards?\b/, /\b(?:target|each|an) opponent discards?\b/],
  ["mill", "self-mill", /\b(?:you|a player) mills?\b|\bcards? (?:you have )?milled\b/, /\bmill (?:a card|\d+ cards?|that many cards?|x cards?)\b|\byou mill\b|\bsurveil\b/],
  ["graveyard", "stocking your graveyard", /\b(?:from|in|into) your graveyard\b|\bcards? in graveyards?\b/, /\bmill (?:a card|\d+ cards?|that many cards?|x cards?)\b|\byou mill\b|\bsurveil\b|\bdiscard (?:an? |one |two |\d+ )?(?:cards?\b|your hand\b)/],
  ["graveyard-cast", "casting from the graveyard", /\b(?:cast|play)\b[^.]{0,62}\bfrom (?:your|a) graveyard\b/, /\b(?:flashback|escape|disturb|unearth|jump-start|aftermath|retrace)\b|\byou may (?:cast|play)\b[^.]{0,54}\bfrom your graveyard\b/],
  ["exile-cast", "casting from exile", /\b(?:cast|play)\b[^.]{0,65}\bfrom (?:exile|anywhere other than your hand)\b/, /\b(?:rebound|cascade|discover(?: \d+)?|adventure|foretell|plot|suspend \d+)\b|\b(?:gains?|has|have) suspend\b|\byou may (?:cast|play)\b[^.]{0,65}\b(?:exiled cards?|from exile|those cards|that card)\b/],
  ["suspend", "suspended cards", /\bsuspend(?:ed)?\b/, /\bsuspend (?:\d+|x)\b|\b(?:gains?|has|have) suspend\b/],
  ["upkeep", "additional upkeep triggers", /\bbeginning of your upkeep\b|\bsuspend(?:ed)?\b/, /\badditional upkeep step\b/],
  ["counter", "counters on permanents", /\bcounters? on\b|\bcounter (?:is|was) put\b|\bproliferate\b/, /\b(?:put|with|add)\b[^.]{0,35}\bcounters? (?:on|among)\b|\bproliferate\b/],
  ["plus-counter", "+1/+1 counters", /\+1\/\+1 counters?\b/, /(?:\bput\b|\bwith\b|\badd\b)[^.]{0,36}\+1\/\+1 counters?\b|\b(?:adapt|fabricate|support|backup) \d+\b/],
  ["time-counter", "time counters", /\btime counters?\b|\btime travel\b/, /\b(?:put|with)\b[^.]{0,36}\btime counters?\b|\b(?:vanishing|suspend) \d+\b/],
  ["time-advance", "advancing a suspend countdown", /\bsuspend(?:ed)?\b/, /\btime travel\b|\bremove\b[^.]{0,45}\bcounters?\b[^.]{0,50}\bsuspended cards?\b|\badditional upkeep step\b/],
  ["experience", "experience counters", /\bexperience counters?\b/, /\bget\b[^.]{0,24}\bexperience counters?\b/],
  ["energy", "energy", /\benergy counters?\b|\{e\}/, /\bget (?:\{e\}|energy counters?)|\badd\b[^.]{0,20}\{e\}/],
  ["poison", "poison counters", /\b(?:poison counters?|corrupted)\b/, /\btoxic \d+\b|\binfect\b|\bget\b[^.]{0,30}\bpoison counters?\b/],
  ["attach", "attaching Equipment and Auras", /\b(?:becomes? attached|you attach|equipped|modified|enchanted)\b/, /\b(?:equip|reconfigure|bestow)\b|\battach\b[^.]{0,50}\b(?:equipment|aura)\b/],
  ["copy-spell", "copying spells", /\b(?:you copy|a copy of)\b[^.]{0,40}\bspells?\b/, /\bcopy (?:it|that spell|target[^.]{0,34}spell|an? (?:instant|sorcery) spell)\b|\b(?:storm|demonstrate|casualty)\b/],
  ["blink", "reusing enter-the-battlefield abilities", /\b(?:this card|this creature|this artifact|this enchantment) enters\b/, /\bexile\b[^.]{0,115}\b(?:return|returns)\b[^.]{0,100}\bbattlefield\b|\b(?:flicker|blink)\b/],
  ["untap", "reusing tap abilities", /\{t\}[^.]{0,24}:/, /\buntap\b[^.]{0,60}\b(?:target|all|each|another)\b[^.]{0,35}\b(?:creatures?|permanents?|artifacts?|lands?)\b/],
  ["vehicle", "Vehicles", /\bvehicles?\b/, null],
  ["historic", "historic spells", /\bhistoric (?:spells?|permanents?|cards?)\b/, null],
];

const CARD_TYPES = ["artifact", "battle", "creature", "enchantment", "instant", "kindred", "land", "planeswalker", "sorcery"];
const KEYWORDS = ["deathtouch", "defender", "first strike", "flying", "haste", "hexproof", "indestructible", "lifelink", "menace", "reach", "trample", "vigilance", "ward"];
const TYPE_WORDS = new Set([...CARD_TYPES, ...KEYWORDS, ...(
  "a an and any are at be basic black blue by can card cast colorless control create do each for from gain get green " +
  "have if in is it legendary more nontoken nonlegendary of on one opponent or other permanent play put red return " +
  "sacrifice same snow some spell tapped target than that the them then this three to token turn two under untapped " +
  "was when whenever white with would you your"
).split(" ")]);
const TYPE_MATCHERS = [...CARD_TYPES, "legendary", "snow"].map((type) => [type, new RegExp(`(?:^|[^\\w-])${type}(?:s)?(?=[^\\w-]|$)`)]);
const KEYWORD_MATCHERS = KEYWORDS.map((keyword) => [keyword, new RegExp(`\\bwith ${keyword}\\b`)]);
const IRREGULAR = { elves: "elf", dwarves: "dwarf", wolves: "wolf", werewolves: "werewolf", mice: "mouse", geese: "goose", oxen: "ox", merfolk: "merfolk", sheep: "sheep", mongoose: "mongoose", pegasus: "pegasus", cyclops: "cyclops", fungus: "fungus", fungi: "fungus" };
const TOPIC_LABELS = new Map(TOPICS.map(([id, label]) => [id, label]));

function lower(value) {
  return typeof value === "string" ? value.normalize("NFKC").toLowerCase().replaceAll("’", "'") : "";
}

function foldSubtype(word) {
  return IRREGULAR[word] || word.replace(/(?<!s)s$/, "");
}

function subtypeWords(value) {
  return (value.replaceAll("time lord", "time-lord").match(/[a-z][a-z-]+/g) || [])
    .map(foldSubtype).filter((word) => !TYPE_WORDS.has(word));
}

function put(map, key, weight = 1) {
  map.set(key, Math.max(weight, map.get(key) || 0));
}

function merge(target, source) {
  for (const [key, weight] of source) put(target, key, weight);
}

function faceList(card) {
  const faces = card.card_faces || card.faces;
  return Array.isArray(faces) && faces.length > 0 ? faces : [card];
}

function faceText(face, parent) {
  const name = (face.name || parent.name || "").normalize("NFKC").replaceAll("’", "'");
  const rules = (face.oracle_text ?? face.oracleText ?? face.text ?? "").normalize("NFKC").replaceAll("’", "'");
  let text = rules.replace(/\([^()]*\)/g, "");
  for (const form of new Set([name, name.split(",", 1)[0]])) {
    if (!form) continue;
    const escaped = form.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    text = text.replace(new RegExp(`(?<![\\w-])${escaped}(?![\\w-])`, "g"), "this card");
  }
  return lower(text);
}

function typeProfile(typeLine, gives, tribes) {
  const [head, tail = ""] = lower(typeLine).split(/\s[—–-]\s/, 2);
  const types = head.match(/[a-z]+/g) || [];
  for (const type of CARD_TYPES) {
    if (types.includes(type)) put(gives, `type:${type}`, .7);
  }
  for (const type of ["legendary", "snow"]) {
    if (types.includes(type)) put(gives, `type:${type}`, .8);
  }
  for (const tribe of subtypeWords(tail)) put(tribes, tribe, .87);
  if (types.includes("legendary") || types.includes("artifact") || /\bsaga\b/.test(tail)) put(gives, "historic", .73);
  if (/\bvehicle\b/.test(tail)) put(gives, "vehicle", .85);
  return types;
}

function subtypePreferences(condition, wantsTribes) {
  // The typed subject of a trigger or a lord ability is the demand. Words in
  // the token-creating result are handled separately as supply.
  for (const candidate of subtypeWords(condition)) wantsTribes.add(candidate);
}

function typePreferences(condition, wants) {
  for (const [type, pattern] of TYPE_MATCHERS) {
    if (pattern.test(condition)) put(wants, `type:${type}`, .9);
  }
}

function isOwnPrerequisite(text) {
  const opposing = /\b(?:an?|each|your|target) opponent\b|\battacks? you\b|\bdeals? combat damage to you\b|\byou (?:don't|do not) control\b/;
  return !opposing.test(text) || /\b(?:you or|you and|your opponents? and you)\b/.test(text);
}

function scoreCondition(clause, profile, strength = 1) {
  const own = isOwnPrerequisite(clause);
  const tokenSacrifice = /\btokens?\b[^.]{0,35}\bsacrificed\b|\bsacrifice\b[^.]{0,24}\btokens?\b/.test(clause);
  for (const [topic, , prerequisite] of TOPICS) {
    if (topic === "sacrifice" && tokenSacrifice) continue;
    if ((own || topic.startsWith("opponent-")) && prerequisite.test(clause)) put(profile.wants, topic, strength);
  }
  if (own && !/\bthis (?:card|creature|artifact|enchantment|land) enters\b/.test(clause)) {
    typePreferences(clause, profile.wants);
    subtypePreferences(clause, profile.wantsTribes);
  }
}

function scoreEffect(clause, profile) {
  const opponentActs = /\b(?:an?|each|your|target) opponent\b[^.]{0,18}\b(?:creates?|draws?|mills?|gains?|adds?|returns?|puts?)\b/.test(clause);
  for (const [topic, , effect] of TOPICS) {
    if (!effect?.test(clause)) continue;
    if (!opponentActs || ["damage", "opponent-discard", "opponent-life-loss"].includes(topic)) {
      put(profile.gives, topic, 1);
    }
  }
  if (opponentActs) return;
  const creatureTokens = clause.matchAll(/\bcreat(?:e|es)\b([^.;]{0,120})\bcreature tokens?\b/g);
  for (const match of creatureTokens) {
    for (const word of subtypeWords(match[1]).slice(-3)) put(profile.givesTribes, word, .67);
    put(profile.gives, "type:creature", .7);
    put(profile.gives, "dies", .45);
    put(profile.gives, "attack", .6);
  }
  if (/\b(?:create|creates)\b[^.]{0,95}\b(?:treasure|food|clue|blood|powerstone|artifact) tokens?\b/.test(clause) || /\binvestigate\b/.test(clause)) {
    put(profile.gives, "type:artifact", .8);
    put(profile.gives, "token", 1);
    put(profile.gives, "artifact-graveyard", .58);
  }
}

function readClause(clause, profile) {
  const trigger = clause.match(/^(?:[^.,:]{0,45}\s[—–-]\s)?(?:when(?:ever)?|at the beginning of|if)\s+([^,.;]{1,170}),\s*(.*)$/);
  if (trigger) {
    scoreCondition(trigger[1], profile);
    scoreEffect(trigger[2], profile);
  } else {
    scoreEffect(clause, profile);
  }

  const cost = clause.match(/^([^:.]{1,125}):/);
  if (cost && !trigger) {
    if (/\{t\}/.test(cost[1])) put(profile.wants, "untap", .75);
    if (/\b(?:sacrifice|discard)\b/.test(cost[1])) {
      scoreEffect(cost[1], profile);
      if (/\bsacrifice\b[^.]{0,50}\b(?:creatures?|tokens?)\b/.test(cost[1])) put(profile.wants, "creature-token", .68);
      if (/\bsacrifice\b[^.]{0,50}\bartifacts?\b/.test(cost[1])) put(profile.wants, "type:artifact", .65);
    }
  }

  for (const match of clause.matchAll(/\b(?:for each|the number of) ([^,.;]{1,85}?) (?:you control|in your graveyard|you own)\b/g)) {
    scoreCondition(match[1], profile, .95);
  }
  for (const match of clause.matchAll(/(?:^|[.;])\s*(?:other |all |each |another )?([^,.;:]{1,45}) you control (?:get|gets|have|has|gain|gains|are|cost)\b/g)) {
    scoreCondition(match[1], profile, .9);
  }
  // Casting cards from the graveyard or making them targets asks for fuel,
  // while generic conditional spell copy needs enough of the noted card type.
  if (/\b(?:return|cast|exile)\b[^.]{0,80}\bfrom your graveyard\b/.test(clause)) put(profile.wants, "graveyard", .83);
  if (/\baffinity for\b/.test(clause) || /\bspells? you cast costs?\b/.test(clause)) {
    typePreferences(clause, profile.wants);
    subtypePreferences(clause.split(/\b(?:spells?|you cast|costs?)\b/, 1)[0], profile.wantsTribes);
  }
  if (/\b(?:creatures?|permanents?)\b[^.]{0,45}\bwith\b/.test(clause)) {
    for (const [keyword, pattern] of KEYWORD_MATCHERS) {
      if (pattern.test(clause) && !/\b(?:create|creates)\b/.test(clause)) put(profile.wants, `keyword:${keyword}`, .85);
    }
  }
  if (/\b(?:this card|this creature|this artifact|this enchantment) enters\b/.test(clause)) put(profile.wants, "blink", .85);
  if (/\b(?:gains?|has|have) suspend\b|\bsuspend (?:\d+|x)\b/.test(clause)) {
    put(profile.wants, "time-advance", .88);
    put(profile.gives, "exile-cast");
  }
  if (/\bproliferate\b/.test(clause)) put(profile.wants, "counter", .65);
}

export function oracleProfile(card) {
  const profile = { gives: new Map(), wants: new Map(), givesTribes: new Map(), wantsTribes: new Set() };
  const keywords = new Set((card.keywords || []).map(lower));
  const faces = faceList(card);
  for (const face of faces) {
    const types = typeProfile(face.type_line || face.typeLine || card.type_line || card.typeLine, profile.gives, profile.givesTribes);
    const rules = faceText(face, card);
    const manaValue = Number(card.cmc ?? card.mana_value ?? card.manaValue);
    if (types.includes("creature") && !keywords.has("defender") && !/^defender\b/m.test(rules)) {
      put(profile.gives, "attack", manaValue <= 3 ? .62 : .35);
      put(profile.gives, "dies", .34);
    }
    for (const clause of rules.split(/[.;\n]/).map((value) => value.trim()).filter(Boolean)) readClause(clause, profile);
    if (/\bsuspended cards?\b/.test(rules) && /\bremove\b[^.]{0,40}\bcounters?\b/.test(rules)) {
      put(profile.gives, "time-advance");
      put(profile.wants, "suspend", .78);
    }
  }
  for (const keyword of KEYWORDS) {
    if (keywords.has(keyword)) put(profile.gives, `keyword:${keyword}`, .68);
  }
  if (keywords.has("lifelink")) put(profile.gives, "life-gain", .74);
  if (["flying", "menace", "trample", "shadow", "skulk"].some((word) => keywords.has(word))) put(profile.gives, "evasion", .67);
  if (["suspend", "foretell", "plot", "rebound", "cascade", "adventure"].some((word) => keywords.has(word))) put(profile.gives, "exile-cast", .95);
  if (keywords.has("landfall")) put(profile.wants, "landfall");
  return profile;
}

export function commanderOracleProfile(cards) {
  const profile = { gives: new Map(), wants: new Map(), givesTribes: new Map(), wantsTribes: new Set() };
  for (const card of cards) {
    const current = oracleProfile(card);
    merge(profile.gives, current.gives);
    merge(profile.wants, current.wants);
    merge(profile.givesTribes, current.givesTribes);
    for (const tribe of current.wantsTribes) profile.wantsTribes.add(tribe);
  }
  return profile;
}

function topicLabel(id) {
  if (TOPIC_LABELS.has(id)) return TOPIC_LABELS.get(id);
  const [kind, value] = id.split(":");
  return kind === "type" ? `${value} cards` : value;
}

export function oracleSynergy(commanders, candidate, coverage) {
  const matches = [];
  function add(id, score, direction) {
    const seen = coverage instanceof Map ? coverage.get(id) : coverage?.[id];
    const weight = score / (1 + .09 * (Number.isFinite(seen) && seen > 0 ? seen : 0));
    matches.push({ id, label: topicLabel(id), direction, score: weight });
  }
  for (const [id, want] of commanders.wants) {
    const give = candidate.gives.get(id);
    if (give) add(id, want * give, "supports-commanders");
  }
  for (const [id, want] of candidate.wants) {
    const give = commanders.gives.get(id);
    if (give) add(id, .78 * want * give, "supported-by-commanders");
  }
  for (const [tribe, give] of candidate.givesTribes) {
    if (commanders.wantsTribes.has(tribe)) add(`kind:${tribe}`, 1.28 * give, "supports-commanders");
  }
  for (const [tribe, give] of commanders.givesTribes) {
    if (candidate.wantsTribes.has(tribe)) add(`kind:${tribe}`, .92 * give, "supported-by-commanders");
  }
  matches.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
  const strongest = new Map();
  for (const match of matches) {
    if (!strongest.has(match.id)) strongest.set(match.id, match);
  }
  const unique = [...strongest.values()];
  const best = unique[0];
  return {
    score: unique.slice(0, 4).reduce((sum, match, index) => sum + match.score * (1 - .15 * index), 0),
    matches: unique,
    reason: best ? (best.direction === "supports-commanders"
      ? `Supports the commander plan through ${best.label}.`
      : `Rewards a commander effect involving ${best.label}.`) : null,
  };
}
