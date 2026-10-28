/* Ofrenda, front of house.
   No frameworks, no build step, no requests except to the server on this machine. */

const $ = (sel) => document.querySelector(sel);
const PHOTO_KEY = "ofrenda.photo";

const state = {
  cards: [],
  person: {},
  backend: { model: "no model", name: "none", online: false },
  suggestions: [],
  disagreements: [],
  altars: [],
  limits: { max_card_chars: 420, max_cards: 24 },
};

const night = { lit: false, ended: false, busy: false };

/* ---------- small helpers ---------- */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "the altar did not answer");
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scrollDown() {
  const box = $("#transcript");
  box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
}

function displayName() {
  const name = (state.person.name || "").trim();
  return name || "someone";
}

function dates() {
  const { born, died } = state.person;
  if (born && died) return `${born} to ${died}`;
  return born || died || "";
}

/* ---------- painting the altar ---------- */

function paintPerson() {
  const name = displayName().toUpperCase();
  const plaque = $("#plaque-name");
  plaque.textContent = name.length > 18 ? name.slice(0, 18) : name;
  plaque.setAttribute("font-size", name.length > 14 ? "8.4" : name.length > 10 ? "9.4" : "10.5");
  $("#plaque-dates").textContent = dates().toUpperCase() || "";
  $("#monogram").textContent = name.charAt(0) || "·";

  const who = $("#who");
  who.textContent = night.lit
    ? `${displayName()}${dates() ? ", " + dates() : ""}`
    : "the altar is dark";

  const photo = localStorage.getItem(PHOTO_KEY);
  const image = $("#portrait");
  if (photo) {
    image.setAttribute("href", photo);
    image.style.display = "";
    $("#monogram").style.display = "none";
  } else {
    image.style.display = "none";
    $("#monogram").style.display = "";
  }
}

function paintSignal() {
  const node = $("#signal");
  const text = $("#signal-text");
  node.classList.remove("on", "off");
  if (state.backend.online) {
    node.classList.add("on");
    const model = String(state.backend.model || "local model").replace(/\.gguf$/i, "");
    text.textContent = `${model} · ${state.backend.name} · offline`;
  } else {
    node.classList.add("off");
    text.textContent = "cards only · no model";
  }
}

function paintStrip() {
  const strip = $("#cardstrip");
  strip.replaceChildren();
  state.cards.slice(0, 12).forEach((card) => {
    const node = el("div", "mini-card");
    node.title = card.text;
    node.append(el("span", "mini-text", card.text));
    const by = [card.by, card.year].filter(Boolean).join(", ");
    node.append(el("span", "mini-by", by || "unattributed"));
    strip.append(node);
  });
  if (state.cards.length < state.limits.max_cards) {
    const add = el("button", "mini-card empty", "leave another memory");
    add.type = "button";
    add.addEventListener("click", () => openCards());
    strip.append(add);
  }
}

function paintWho() {
  paintPerson();
  const lit = night.lit && !night.ended;
  $("#question").disabled = !lit;
  $("#send").disabled = !lit;
  $("#open-cards").hidden = night.ended;
  $("#end-night").hidden = !lit || night.ended;
  $("#light").disabled = night.busy;
}

function paintChips() {
  const box = $("#chips");
  box.replaceChildren();
  const words = state.suggestions.slice(0, 3);
  const show = night.lit && !night.ended && (words.length > 0 || state.disagreements.length > 0);
  box.hidden = !show;
  if (!show) return;
  words.forEach((word) => {
    const chip = el("button", "chip", word);
    chip.type = "button";
    chip.addEventListener("click", () => ask(`Ask about ${word}`));
    box.append(chip);
  });
  if (state.disagreements.length) {
    const chip = el("button", "chip", "which memory is right");
    chip.type = "button";
    chip.addEventListener("click", () => ask("Two of the cards disagree. What really happened?"));
    box.append(chip);
  }
}

function paintInvitation() {
  const box = $("#invitation");
  if (!box) return;
  if (night.lit || night.ended) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const empty = state.cards.length === 0;
  box.querySelector(".invite-line").textContent = empty
    ? "The altar is bare."
    : `${displayName()} is waiting.`;
  box.querySelector(".invite-sub").textContent = empty
    ? "Leave one memory first. The altar can only speak from what the family writes down."
    : "Light the candles and the cards will speak for the people who wrote them. The model that carries their words runs from a file on this machine, so the night works with the internet off.";
  const button = $("#light");
  button.lastChild.textContent = empty ? " Leave the first memory" : " Light the candles";
}

function applyState(next) {
  Object.assign(state, next);
  paintPerson();
  paintSignal();
  paintStrip();
  paintWho();
  paintChips();
  paintInvitation();
  paintCardList();
  fillPersonForm();
}

/* ---------- transcript ---------- */

function addTurn(turn) {
  const box = $("#transcript");
  const node = el("div", "turn");
  if (turn.kind === "opening") node.classList.add("opening");
  if (!turn.echo) node.classList.add("refusal");

  const folded = (value) => (value || "").toLowerCase().replace(/[^a-z0-9áéíóúñü ]/g, " ").replace(/\s+/g, " ").trim();
  const quoted = turn.echo && turn.source !== "verbatim" && !folded(turn.say).includes(folded(turn.echo));
  if (quoted) node.append(el("p", "echo", `“${turn.echo}”`));
  const say = el("p", "say", turn.say);
  node.append(say);

  const attrib = el("p", "attrib");
  if (quoted) {
    const who = [turn.by, turn.year].filter(Boolean).join(", ");
    attrib.append(document.createTextNode(`borrowed from card ${turn.number}${who ? ", " + who : ""}`));
  } else if (turn.number === 0) {
    attrib.append(document.createTextNode("not on the cards"));
  } else {
    const who = [turn.by, turn.year].filter(Boolean).join(", ");
    const lead = turn.source === "verbatim" ? "card" : "kept close to card";
    attrib.append(document.createTextNode(`${lead} ${turn.number}${who ? ", " + who : ""}`));
  }
  if (turn.source === "verbatim") {
    const tag = el("span", "tag", "the card itself");
    tag.title = turn.note
      ? `The model's answer was refused (${turn.note}), so the card was read instead.`
      : "No model wrote this line. The card was read instead.";
    attrib.append(tag);
  } else if (turn.source === "repaired") {
    const tag = el("span", "tag", "second try, checked");
    tag.title = "The first answer failed the check against the cards, so it was sent back with the phrase fixed.";
    attrib.append(tag);
  }
  if (turn.seconds && turn.source !== "verbatim") {
    const seconds = turn.seconds >= 10 ? Math.round(turn.seconds) : turn.seconds.toFixed(1);
    const tag = el("span", "tag", `${seconds}s`);
    tag.title = `${turn.tokens} tokens from the local model in ${turn.seconds} seconds`;
    attrib.append(tag);
  }
  node.append(attrib);
  box.append(node);
  scrollDown();
}

function addQuestion(text) {
  const node = el("div", "turn family");
  node.append(el("p", "", text));
  $("#transcript").append(node);
  scrollDown();
}

function addListening() {
  const node = el("div", "listening");
  node.id = "listening";
  node.append(el("span"), el("span"), el("span"));
  node.append(el("em", "", "the candle leans"));
  $("#transcript").append(node);
  scrollDown();
  return node;
}

function addNotice(text) {
  const node = el("div", "turn");
  node.append(el("p", "say", text));
  $("#transcript").append(node);
  scrollDown();
}

/* ---------- the night ---------- */

async function light() {
  if (night.busy) return;
  if (!state.cards.length) {
    openCards();
    return;
  }
  night.busy = true;
  paintWho();
  document.body.classList.add("lit");
  const invitation = $("#invitation");
  if (invitation) invitation.hidden = true;
  const listening = addListening();
  const slow = setTimeout(() => {
    const note = el("p", "say", "The first answer is slow. The model is reading the whole record before it speaks.");
    note.style.opacity = ".6";
    note.style.fontSize = "14px";
    listening.after(note);
  }, 9000);
  try {
    const data = await api("/api/night/open", { method: "POST", body: "{}" });
    clearTimeout(slow);
    listening.remove();
    night.lit = true;
    night.ended = false;
    addTurn(data.turn);
    await refresh();
  } catch (error) {
    clearTimeout(slow);
    listening.remove();
    addNotice(`The candles did not catch: ${error.message}`);
    document.body.classList.remove("lit");
  } finally {
    night.busy = false;
    paintWho();
    paintChips();
  }
}

async function ask(question) {
  const text = (question || "").trim();
  if (!text || night.busy || !night.lit) return;
  night.busy = true;
  $("#question").value = "";
  addQuestion(text);
  const listening = addListening();
  paintWho();
  try {
    const data = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question: text }),
    });
    listening.remove();
    addTurn(data.turn);
    state.stats = data.stats;
    const next = await refresh();
    if ((next.suggestions || []).length) paintChips();
  } catch (error) {
    listening.remove();
    addNotice(`Nothing came back: ${error.message}`);
  } finally {
    night.busy = false;
    paintWho();
    paintChips();
    $("#question").focus();
  }
}

async function endNight() {
  if (night.busy || !night.lit) return;
  night.busy = true;
  const listening = addListening();
  try {
    const data = await api("/api/night/close", { method: "POST", body: "{}" });
    listening.remove();
    addTurn(data.turn);
    night.ended = true;
    document.body.classList.remove("lit");
    showKeepsake(data.keepsake, data.turn.say);
    paintWho();
    paintChips();
    const again = el("button", "ghost-btn relight", "Light them again");
    again.type = "button";
    again.addEventListener("click", async () => {
      again.remove();
      night.ended = false;
      night.lit = false;
      document.body.classList.remove("lit");
      await refresh();
      light();
    });
    $("#transcript").append(again);
    scrollDown();
  } catch (error) {
    listening.remove();
    addNotice(`The night would not close: ${error.message}`);
  } finally {
    night.busy = false;
    paintWho();
  }
}

/* ---------- record keeping ---------- */

async function refresh() {
  const next = await api("/api/state");
  applyState(next);
  return next;
}

function openCards() {
  paintCardList();
  fillPersonForm();
  $("#cards").showModal();
}

function paintCardList() {
  const list = $("#card-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.cards.length) {
    const row = el("li", "");
    row.append(el("span", "empty-row", "No cards yet. The altar has nothing to say until you write one."));
    list.append(row);
    return;
  }
  state.cards.forEach((card, index) => {
    const row = el("li");
    row.append(el("span", "n", String(index + 1).padStart(2, "0")));
    const wrap = el("div");
    wrap.append(el("span", "t", card.text));
    const by = [card.by, card.year].filter(Boolean).join(", ");
    wrap.append(el("span", "m", by || "unattributed"));
    row.append(wrap);
    const kill = el("button", "kill", "remove");
    kill.type = "button";
    kill.addEventListener("click", async () => {
      if (!confirm("Take this card off the altar?")) return;
      try {
        const data = await api(`/api/cards?id=${encodeURIComponent(card.id)}`, { method: "DELETE" });
        applyState(data);
        if (!data.cards.length) closeNightVisuals();
      } catch (error) {
        alert(error.message);
      }
    });
    row.append(kill);
    list.append(row);
  });
}

function closeNightVisuals() {
  night.lit = false;
  night.ended = false;
  document.body.classList.remove("lit");
  paintWho();
  paintChips();
}

async function addCard(event) {
  event.preventDefault();
  const text = $("#card-text").value.trim();
  const by = $("#card-by").value.trim();
  const year = $("#card-year").value.trim();
  if (text.length < 3) return;
  try {
    const data = await api("/api/cards", {
      method: "POST",
      body: JSON.stringify({ text, by, year }),
    });
    $("#card-text").value = "";
    $("#card-year").value = "";
    applyState(data.state);
    paintCardList();
  } catch (error) {
    $("#card-hint").textContent = error.message;
  }
}

function fillPersonForm() {
  const p = state.person;
  $("#p-name").value = p.name || "";
  $("#p-called").value = p.called || "";
  $("#p-born").value = p.born || "";
  $("#p-died").value = p.died || "";
  $("#p-relation").value = p.relation || "";
}

async function savePerson() {
  const body = {
    name: $("#p-name").value,
    called: $("#p-called").value,
    born: $("#p-born").value,
    died: $("#p-died").value,
    relation: $("#p-relation").value,
  };
  const data = await api("/api/person", { method: "POST", body: JSON.stringify(body) });
  applyState(data);
}

/* ---------- keepsake ---------- */

let keepsakeFile = null;

function showKeepsake(file, dedication) {
  keepsakeFile = file;
  const preview = $("#keepsake-preview");
  preview.replaceChildren();
  preview.append(el("p", "kp-kicker", "Recuerdo"));
  preview.append(el("h3", "kp-name", state.person.name || "Someone"));
  preview.append(el("p", "kp-dates", dates()));
  preview.append(el("p", "kp-line", dedication));
  state.cards.slice(0, 4).forEach((card) => {
    const quote = el("p", "kp-quote", card.text);
    const by = [card.by, card.year].filter(Boolean).join(", ");
    quote.append(document.createTextNode("  "));
    quote.append(el("span", "", by || "unattributed"));
    preview.append(quote);
  });
  $("#keepsake").showModal();
}

function downloadKeepsake() {
  if (!keepsakeFile) return;
  const blob = new Blob([keepsakeFile.html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = keepsakeFile.filename || "recuerdo.html";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/* ---------- welcome ---------- */

function paintAltarChoices() {
  const box = $("#altar-choices");
  box.replaceChildren();
  const current = (state.person.name || "").trim().toLowerCase();
  state.altars.forEach((altar) => {
    const button = el("button", "altar-choice");
    button.type = "button";
    if ((altar.person.name || "").trim().toLowerCase() === current) {
      button.classList.add("active");
    }
    button.append(el("span", "ac-name", altar.person.name || altar.label));
    const span = [altar.person.born, altar.person.died].filter(Boolean).join(" to ");
    button.append(el("span", "ac-dates", `${span}  ·  ${altar.cards} cards`));
    button.append(el("span", "ac-blurb", altar.blurb || altar.label));
    button.addEventListener("click", async () => {
      const data = await api("/api/altars", {
        method: "POST",
        body: JSON.stringify({ id: altar.id }),
      });
      applyState(data);
      $("#welcome").close();
      resetTranscript();
      closeNightVisuals();
      paintInvitation();
    });
    box.append(button);
  });
}

function resetTranscript() {
  const box = $("#transcript");
  box.replaceChildren();
  const invitation = el("div", "invitation");
  invitation.id = "invitation";
  invitation.append(el("p", "invite-line", "Nobody is here yet."));
  invitation.append(el("p", "invite-sub", ""));
  const button = el("button", "light");
  button.id = "light";
  button.type = "button";
  button.append(el("span", "light-flame"));
  button.append(document.createTextNode(" Light the candles"));
  button.addEventListener("click", light);
  invitation.append(button);
  box.append(invitation);
  paintInvitation();
}

/* ---------- petals ---------- */

function scatterPetals() {
  const box = $("#petals");
  const count = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 14;
  for (let i = 0; i < count; i += 1) {
    const petal = el("i", "petal");
    petal.style.left = `${6 + Math.random() * 88}%`;
    petal.style.setProperty("--dur", `${12 + Math.random() * 12}s`);
    petal.style.setProperty("--delay", `${-Math.random() * 18}s`);
    petal.style.transform = `scale(${0.7 + Math.random() * 0.7})`;
    box.append(petal);
  }
}

/* ---------- wiring ---------- */

function wire() {
  $("#light").addEventListener("click", light);
  $("#ask").addEventListener("submit", (event) => {
    event.preventDefault();
    ask($("#question").value);
  });
  $("#end-night").addEventListener("click", endNight);
  $("#open-cards").addEventListener("click", openCards);
  $("#cards-close").addEventListener("click", () => $("#cards").close());
  $("#card-form").addEventListener("submit", addCard);
  $("#person-save").addEventListener("click", async () => {
    await savePerson();
    $("#person-save").textContent = "Saved";
    setTimeout(() => ($("#person-save").textContent = "Save the person"), 1400);
  });
  $("#keepsake-download").addEventListener("click", downloadKeepsake);
  $("#keepsake-close").addEventListener("click", () => $("#keepsake").close());
  $("#about-btn").addEventListener("click", () => $("#about").showModal());
  $("#switch-altar").addEventListener("click", () => {
    paintAltarChoices();
    $("#welcome").showModal();
  });
  $("#about-close").addEventListener("click", () => $("#about").close());
  $("#welcome-close").addEventListener("click", () => $("#welcome").close());
  $("#start-empty").addEventListener("click", () => {
    $("#welcome").close();
    openCards();
  });

  $("#photo").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (file.size > 3.2 * 1024 * 1024) {
      alert("Keep the photograph under 3 MB. It has to fit in this browser.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      try {
        localStorage.setItem(PHOTO_KEY, reader.result);
        paintPerson();
      } catch (error) {
        alert("This browser would not hold the photograph.");
      }
    };
    reader.readAsDataURL(file);
  });
  $("#photo-clear").addEventListener("click", () => {
    localStorage.removeItem(PHOTO_KEY);
    paintPerson();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement.tagName !== "INPUT"
        && document.activeElement.tagName !== "TEXTAREA") {
      event.preventDefault();
      $("#question").focus();
    }
  });
}

async function boot() {
  wire();
  scatterPetals();
  try {
    const initial = await api("/api/state");
    applyState(initial);
    paintAltarChoices();
    if (!initial.cards.length) {
      $("#welcome").showModal();
    }
  } catch (error) {
    addNotice(`The altar did not open: ${error.message}`);
  }
}

boot();
