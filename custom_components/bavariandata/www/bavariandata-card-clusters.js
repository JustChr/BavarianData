/*
 * BavarianData Card: cluster lists and the check-control messages.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base, { CLUSTER_ICONS, _lang }) => class ClustersPart extends Base {
  _renderCluster(deviceId, entities) {
    const slug = this._config.cluster;
    const rows = entities
      .map((id) => this._st(id))
      .filter((st) => st && st.attributes && st.attributes.cluster === slug)
      .sort((a, b) =>
        (a.attributes.friendly_name || a.entity_id).localeCompare(
          b.attributes.friendly_name || b.entity_id
        )
      );

    // BMW files Check Control messages ("washer fluid low") under usage-based
    // data, yet they are what a driver means by a vehicle event: the events
    // cluster itself holds only two teleservice timestamps, which most cars
    // never send. So that view leads with the messages.
    const cc = slug === "events" ? this._checkControl(entities) : null;
    const messages = cc ? cc.items : [];

    const label = this._config.title || this._clusterLabel(slug);
    const icon = CLUSTER_ICONS[slug] || "mdi:car";
    const name = this._deviceName(deviceId);
    const count = rows.length + messages.length;

    const sig = this._signature({
      m: "cl",
      lang: _lang(this._hass),
      slug,
      rows: rows.map((s) => [s.entity_id, s.state]),
      cc: cc && [cc.id, cc.items, cc.resolved, cc.updated, cc.unit, this._ccExpanded, this._ccEarlier],
    });
    if (sig === this._sig) return;
    this._sig = sig;

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <div class="chead">
          <ha-icon icon="${icon}"></ha-icon>
          <div class="chead__text">
            <span class="chead__title">${this._esc(label)}</span>
            <span class="chead__sub">${this._esc(name)} · ${count} ${this._t(count === 1 ? "value" : "values")}</span>
          </div>
        </div>
        ${
          cc
            ? `<div class="list__head">${this._t("cc_title")}</div>
              ${
                messages.length
                  ? `<div class="list">${messages.map((msg) => this._ccRow(cc, msg)).join("")}</div>`
                  : `<div class="empty empty--inline">${this._t("cc_none")}</div>`
              }
              ${this._ccEarlierBlock(cc)}
              ${rows.length ? `<div class="list__head">${this._t("cc_teleservice")}</div>` : ""}`
            : ""
        }
        ${
          rows.length
            ? `<div class="list">
                ${rows
                  .map((st) => {
                    return `<button class="item" data-entity="${st.entity_id}">
                      <span class="item__name" title="${this._esc(st.attributes.friendly_name || st.entity_id)}">${this._shortName(st, name)}</span>
                      <span class="item__val">${this._fmt(st)}</span>
                    </button>`;
                  })
                  .join("")}
              </div>`
            : cc
              ? ""
              : `<div class="empty">${this._t("no_cluster_entities", { label: `<b>${this._esc(label)}</b>` })}</div>`
        }
      </ha-card>
    `;
    this._wireTaps();
    // A message is a list item, not an entity: it expands in place, the way a
    // charging session or a trip does, instead of opening more-info.
    this.shadowRoot.querySelectorAll("[data-cc]").forEach((el) => {
      el.addEventListener("click", () => this._toggleCc(el.getAttribute("data-cc")));
    });
    this.shadowRoot.querySelectorAll("[data-cc-earlier]").forEach((el) => {
      el.addEventListener("click", () => this._toggleCcEarlier());
    });
  }

  _toggleCcEarlier() {
    this._ccEarlier = !this._ccEarlier;
    this._sig = null;
    this._render();
  }

  /** Messages the car no longer reports: folded away behind one row, so a
   * cleared warning stops looking current but can still be looked up. */
  _ccEarlierBlock(cc) {
    if (!cc.resolved.length) return "";
    const open = !!this._ccEarlier;
    return `<button class="cc-earlier" data-cc-earlier aria-expanded="${open}">
        <ha-icon icon="${open ? "mdi:chevron-up" : "mdi:chevron-down"}"></ha-icon>
        <span>${this._t("cc_earlier", { n: cc.resolved.length })}</span>
      </button>
      ${open ? `<div class="list list--past">${cc.resolved.map((msg) => this._ccRow(cc, msg, true)).join("")}</div>` : ""}`;
  }

  _toggleCc(key) {
    this._ccExpanded = this._ccExpanded === key ? null : key;
    this._sig = null; // force a repaint with the new expansion state
    this._render();
  }

  /** The Check Control sensor and its messages; null on a car without one. */
  _checkControl(entities) {
    const st = this._byDescriptor(entities, "vehicle.status.checkControlMessages");
    if (!st) return null;
    const attrs = st.attributes || {};
    const odometer = this._byDescriptor(entities, "vehicle.vehicle.travelledDistance");
    const messages = (list) =>
      Array.isArray(list) ? list.filter((msg) => msg && typeof msg === "object") : [];
    return {
      id: st.entity_id,
      items: messages(attrs.items),
      resolved: messages(attrs.resolved),
      updated: attrs.timestamp,
      unit: (odometer && odometer.attributes && odometer.attributes.unit_of_measurement) || "km",
    };
  }

  /** A Check Control field, or null for BMW's placeholders ("-", "NULL"). */
  _ccField(value) {
    if (value == null) return null;
    const text = String(value).trim();
    return text === "" || text === "-" || text.toUpperCase() === "NULL" ? null : text;
  }

  /** One Check Control message. BMW sends the text in English on every install,
   * and on the maintainer's i5 with no title and no date. */
  _ccRow(cc, msg, past = false) {
    const ident = String(msg.id != null ? msg.id : msg.text || "");
    // A message can be current and in the history of an earlier occurrence
    // only in turn, never at once -- but keep the keys apart all the same.
    const key = past ? `past:${ident}:${msg.resolved_at || ""}` : ident;
    const isOpen = this._ccExpanded === key;
    const title = this._ccField(msg.title);
    const text = this._ccField(msg.text);
    const head = title || text || this._ccField(msg.messageType) || "—";
    const day = past ? msg.resolved_at : this._ccField(msg.date);
    const when = day ? this._fmtDay(day) : "";
    const icon = past ? "mdi:check-circle-outline" : "mdi:alert-circle-outline";
    return `<div class="cc${past ? " cc--past" : ""}${isOpen ? " is-open" : ""}">
      <button class="item item--msg" data-cc="${this._attr(key)}" aria-expanded="${isOpen}">
        <ha-icon class="item__icon" icon="${icon}"></ha-icon>
        <span class="item__body">
          <span class="item__text${isOpen ? "" : " item__clamp"}">${this._esc(head)}</span>
          ${title && text && !isOpen ? `<span class="item__sub item__clamp">${this._esc(text)}</span>` : ""}
        </span>
        ${when ? `<span class="item__val">${when}</span>` : ""}
      </button>
      ${isOpen ? this._ccDetail(cc, msg, title && text ? text : "", past) : ""}
    </div>`;
  }

  _ccDetail(cc, msg, body, past = false) {
    const description = this._ccField(msg.description);
    const facts = [];
    // Despite its name this is no distance left: on the maintainer's i5 it was
    // the odometer at the start of the drive on which the car last showed the
    // message (19 596, then 19 599 a drive later, while the car read 19 624).
    const mileage = this._ccField(msg.unitOfLengthRemaining);
    if (mileage && Number.isFinite(Number(mileage))) {
      facts.push([this._t("cc_mileage"), `${this._dec(Number(mileage), 0)} ${this._esc(cc.unit)}`]);
    }
    if (past) {
      // The sensor's own timestamp belongs to the current list, not to this one.
      if (msg.last_reported) facts.push([this._t("cc_reported"), this._fmtDay(msg.last_reported)]);
      if (msg.resolved_at) facts.push([this._t("cc_gone"), this._fmtDay(msg.resolved_at)]);
    } else {
      const sent = this._relTime(cc.updated);
      if (sent) facts.push([this._t("cc_sent"), sent]);
    }
    const code = [this._ccField(msg.messageType), this._ccField(msg.id)].filter(Boolean).join(" ");
    if (code) facts.push([this._t("cc_code"), this._esc(code)]);

    const factRow = facts
      .map(
        ([k, v]) =>
          `<div class="chg__fact"><span class="chg__fact-lbl">${k}</span><span class="chg__fact-val">${v}</span></div>`
      )
      .join("");
    return `
      <div class="chg__detail">
        ${body ? `<p class="cc__text">${this._esc(body)}</p>` : ""}
        ${description ? `<p class="cc__text">${this._esc(description)}</p>` : ""}
        ${factRow ? `<div class="chg__facts">${factRow}</div>` : ""}
      </div>
    `;
  }

  /** Localized display label for a catalogue cluster slug. */
  _clusterLabel(slug) {
    return this._t("cl_" + slug, null, slug);
  }

  _shortName(st, deviceName) {
    let n = st.attributes.friendly_name || st.entity_id;
    if (deviceName && n.startsWith(deviceName + " ")) n = n.slice(deviceName.length + 1);
    return this._esc(n);
  }

  /* ---- charging history ------------------------------------------------- */

  /** Battery health describes a high-voltage battery. On a petrol or diesel
   * car it would sit empty for good and read like a fault, so say why instead
   * -- before any service is called. (Charging and efficiency show the Fuel
   * view there.) Returns whether it painted. */
  _renderIceNotice(deviceId, entities) {
    if (this._drivetrain(entities) !== "ice") return false;
    const name = this._config.title || this._deviceName(deviceId);
    const sig = this._signature({ m: "ice", lang: _lang(this._hass), name });
    if (sig === this._sig) return true;
    this._renderMessage(
      this._esc(this._t("ice_view_title")),
      this._esc(this._t("ice_view_body", { name }))
    );
    this._sig = sig;
    return true;
  }
};
