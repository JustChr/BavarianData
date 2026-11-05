/*
 * BavarianData Card: the card's stylesheet.
 *
 * One part of the card class. bavariandata-card.js loads it from beside itself
 * (see CARD_PARTS there) and mixes it into BavarianDataCard; what it takes from
 * the card's own scope arrives in SHARED.
 */

export default (Base) => class StylesPart extends Base {
  _styles() {
    return `
    <style>
      :host {
        --bmw-charge: #2f80ed;
        --bmw-high: #29a36a;
        --bmw-mid: #e6a417;
        --bmw-low: #d64545;
      }
      ha-card {
        overflow: hidden;
        padding: 0;
      }
      * { box-sizing: border-box; }
      button {
        font: inherit;
        color: inherit;
        background: none;
        border: 0;
        padding: 0;
        text-align: left;
        cursor: pointer;
      }
      button.is-static { cursor: default; }

      /* hero */
      .hero {
        position: relative;
        aspect-ratio: 16 / 9;
        background: var(--secondary-background-color);
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .hero__img {
        width: 100%;
        height: 100%;
        object-fit: contain;
        object-position: center 60%;
      }
      .hero__spin {
        position: absolute; inset: 0;
        background-repeat: no-repeat;
        cursor: grab;
        touch-action: pan-y;
        user-select: none;
      }
      .hero__spin:active { cursor: grabbing; }
      /* The night spin is a cut-out: a dark room with a pool of light under the
         car, the same in light and dark themes, as the night is. */
      .hero--night {
        background: radial-gradient(ellipse 52% 30% at 50% 74%, #464a4f 0%, #2c2e32 55%, #18191b 100%);
      }
      .tirecar__photo { width: 100%; height: 100%; object-fit: contain; display: block; }
      .eq { padding: 4px 16px 14px; }
      .eq__group + .eq__group, .eq__more { margin-top: 12px; }
      .eq__title {
        font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
        color: var(--secondary-text-color);
        margin: 4px 0 4px;
      }
      .eq__row {
        display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
        padding: 5px 0;
        border-bottom: 1px solid var(--divider-color);
        font-size: 0.9rem;
      }
      .eq__row:last-child { border-bottom: none; }
      .eq__name { min-width: 0; overflow-wrap: anywhere; }
      .eq__pic {
        flex: none; align-self: center;
        width: 56px; aspect-ratio: 168 / 95;
        border-radius: 4px;
        background-repeat: no-repeat;
        background-color: var(--secondary-background-color);
      }
      .eq__pic + .eq__name { flex: 1; }
      .eq__pic--none {
        display: flex; align-items: center; justify-content: center;
        color: var(--disabled-text-color);
        --mdc-icon-size: 18px;
      }
      .eq__code { flex: none; font-size: 0.72rem; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .eq__more summary { cursor: pointer; font-size: 0.85rem; color: var(--secondary-text-color); padding: 4px 0; }
      .hero--inset .hero__spin-icon { display: none; }
      .hero__chips {
        position: absolute; left: 16px; top: 52px;
        display: flex; flex-direction: column; align-items: flex-start; gap: 5px;
        max-width: 62%;
        pointer-events: none;
      }
      .hero__chip {
        display: inline-flex; align-items: center; gap: 5px;
        background: rgba(0,0,0,0.55);
        color: #fff;
        border-left: 3px solid var(--chip, transparent);
        border-radius: 999px;
        padding: 3px 10px 3px 7px;
        font-size: 0.74rem;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 100%;
      }
      .hero__chip ha-icon { --mdc-icon-size: 15px; color: var(--chip, #fff); flex: none; }
      .hero__inset {
        position: absolute; right: 12px; bottom: 10px;
        height: 46%;
        aspect-ratio: 948 / 1659;
        filter: drop-shadow(0 1px 3px rgba(0,0,0,0.35));
        pointer-events: none;
      }
      .hero__inset svg { width: 100%; height: 100%; display: block; }
      .hero__mode {
        position: absolute; right: 14px; top: 56px;
        width: 34px; height: 34px;
        display: flex; align-items: center; justify-content: center;
        border: none; border-radius: 50%;
        background: rgba(0,0,0,0.46);
        color: #fff;
        cursor: pointer;
        padding: 0;
      }
      .hero__mode ha-icon { --mdc-icon-size: 19px; }
      .pano-wrap { position: absolute; inset: 0; container-type: size; }
      .pano {
        --half: 50cqw;
        position: absolute; inset: 0;
        overflow: hidden;
        perspective: var(--half);
        cursor: grab;
        touch-action: none;
        user-select: none;
        background: #111;
      }
      .pano:active { cursor: grabbing; }
      .pano__cube {
        position: absolute; left: 50%; top: 50%;
        width: 0; height: 0;
        transform-style: preserve-3d;
      }
      .pano__face {
        position: absolute;
        width: calc(var(--half) * 2 + 2px);
        height: calc(var(--half) * 2 + 2px);
        left: calc(var(--half) * -1 - 1px);
        top: calc(var(--half) * -1 - 1px);
        background-repeat: no-repeat;
        backface-visibility: hidden;
      }
      .hero__spin-icon {
        position: absolute; right: 12px; bottom: 10px;
        --mdc-icon-size: 20px;
        color: var(--secondary-text-color);
        opacity: 0.7;
        pointer-events: none;
      }
      .hero__placeholder {
        --mdc-icon-size: 72px;
        color: var(--disabled-text-color);
      }
      .hero__scrim {
        position: absolute; inset: 0;
        background: linear-gradient(180deg, rgba(0,0,0,0.42) 0%, rgba(0,0,0,0) 34%);
        pointer-events: none;
      }
      .hero__top {
        position: absolute; top: 0; left: 0; right: 0;
        display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;
        padding: 14px 16px;
      }
      .hero__name {
        color: #fff;
        font-size: 1.15rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        text-shadow: 0 1px 3px rgba(0,0,0,0.55);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      /* "trip in progress" badge, bottom-left of the hero so it never competes
         with the vehicle name or the freshness pill above it. */
      .hero__trip {
        position: absolute; left: 16px; bottom: 12px;
        display: inline-flex; align-items: center; gap: 7px;
        max-width: calc(100% - 32px);
        background: rgba(0,0,0,0.46);
        color: #fff;
        border-radius: 999px;
        padding: 5px 12px;
        font-size: 0.74rem;
        font-weight: 600;
        backdrop-filter: blur(3px);
        box-shadow: 0 1px 4px rgba(0,0,0,0.3);
      }
      .hero__trip-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: var(--bmw-charge, #4cc2ff);
        flex: 0 0 auto;
        animation: bd-trip-pulse 2s ease-in-out infinite;
      }
      .hero__trip-sub {
        font-weight: 500;
        opacity: 0.85;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      @keyframes bd-trip-pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.45; transform: scale(0.82); }
      }
      /* Respect a reduced-motion preference: the badge still reads as live from
         its colour, so the pulse is simply dropped. */
      @media (prefers-reduced-motion: reduce) {
        .hero__trip-dot { animation: none; }
      }
      .pill {
        display: inline-flex; align-items: center; gap: 6px;
        background: rgba(0,0,0,0.38);
        color: #fff;
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 0.72rem;
        font-weight: 500;
        backdrop-filter: blur(3px);
        white-space: nowrap;
      }
      .dot { width: 7px; height: 7px; border-radius: 50%; }
      .dot--live { background: #37d67a; box-shadow: 0 0 0 0 rgba(55,214,122,0.6); animation: pulse 2.6s infinite; }
      .dot--stale { background: #c9a227; }
      @keyframes pulse {
        0% { box-shadow: 0 0 0 0 rgba(55,214,122,0.55); }
        70% { box-shadow: 0 0 0 6px rgba(55,214,122,0); }
        100% { box-shadow: 0 0 0 0 rgba(55,214,122,0); }
      }

      /* band: gauge + lead metrics */
      .band {
        display: flex;
        align-items: center;
        gap: 18px;
        padding: 18px 18px 8px;
      }
      .gauge {
        position: relative;
        flex: 0 0 auto;
      }
      .gauge__ring {
        width: 96px; height: 96px;
        border-radius: 50%;
        background:
          radial-gradient(closest-side, var(--card-background-color) 70%, transparent 71% 100%),
          conic-gradient(var(--ring) calc(var(--pct) * 1%), var(--divider-color) 0);
        display: grid; place-items: center;
        transition: background 0.6s ease;
      }
      .gauge__hole { text-align: center; line-height: 1; }
      .gauge__val {
        font-size: 1.7rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
      }
      .gauge__val i { font-size: 0.85rem; font-weight: 500; font-style: normal; color: var(--secondary-text-color); margin-left: 1px; }
      .gauge__cap {
        display: block; margin-top: 3px;
        font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.09em;
        color: var(--secondary-text-color);
      }
      .gauge__bolt {
        position: absolute; right: -2px; top: -2px;
        --mdc-icon-size: 20px;
        color: var(--bmw-charge);
        background: var(--card-background-color);
        border-radius: 50%;
        padding: 2px;
      }

      .lead { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
      .lead__row {
        display: grid;
        grid-template-columns: 24px 1fr;
        grid-template-rows: auto auto;
        column-gap: 10px;
        align-items: center;
        border-radius: 10px;
        padding: 6px 8px;
        transition: background 0.15s ease;
      }
      .lead__row:hover { background: var(--secondary-background-color); }
      .lead__row ha-icon { grid-row: 1 / 3; color: var(--secondary-text-color); --mdc-icon-size: 22px; }
      .lead__val {
        font-size: 1.05rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .lead__lbl { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--secondary-text-color); }

      /* metric grid */
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
        gap: 8px;
        padding: 8px 14px 16px;
      }
      .cell {
        display: flex; align-items: center; gap: 10px;
        padding: 10px 12px;
        border-radius: 12px;
        background: var(--secondary-background-color);
        transition: transform 0.12s ease, background 0.15s ease;
      }
      .cell:hover { background: var(--divider-color); }
      .cell:active { transform: scale(0.98); }
      .cell ha-icon { color: var(--secondary-text-color); --mdc-icon-size: 22px; flex: 0 0 auto; }
      .cell__body { min-width: 0; display: flex; flex-direction: column; }
      .cell__val {
        font-size: 0.98rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      .cell__lbl { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--secondary-text-color); }

      /* cluster mode */
      .chead {
        display: flex; align-items: center; gap: 12px;
        padding: 16px 18px;
        border-bottom: 1px solid var(--divider-color);
      }
      .chead > ha-icon { --mdc-icon-size: 26px; color: var(--bmw-charge); }
      .chead__text { display: flex; flex-direction: column; }
      .chead__title { font-size: 1.05rem; font-weight: 600; color: var(--primary-text-color); }
      .chead__sub { font-size: 0.74rem; color: var(--secondary-text-color); }
      /* export buttons, pushed to the right edge of the header */
      .xbar { margin-left: auto; display: flex; gap: 6px; flex-shrink: 0; }

      /* month navigator, between the header and the list it scopes */
      .mnav {
        display: flex; align-items: center; justify-content: center; gap: 4px;
        padding: 8px 14px 2px;
      }
      .mnav__label {
        min-width: 11ch; text-align: center;
        font-size: 0.8rem; font-weight: 600; color: var(--primary-text-color);
      }
      .mnav__btn {
        display: inline-flex; align-items: center; justify-content: center;
        width: 28px; height: 28px; padding: 0;
        border: 1px solid var(--divider-color); border-radius: 999px;
        background: transparent; color: var(--secondary-text-color);
        cursor: pointer; font: inherit;
      }
      .mnav__btn > ha-icon { --mdc-icon-size: 18px; }
      .mnav__btn:hover:not([disabled]) {
        border-color: var(--primary-color); color: var(--primary-color);
      }
      /* Forward past the current month leads nowhere, so the control says so
         rather than silently doing nothing. */
      .mnav__btn[disabled] { opacity: 0.35; cursor: default; }
      .xbtn {
        display: inline-flex; align-items: center; gap: 4px;
        padding: 5px 10px 5px 7px;
        font: inherit; font-size: 0.74rem; font-weight: 500;
        color: var(--secondary-text-color);
        background: var(--secondary-background-color);
        border: 1px solid var(--divider-color); border-radius: 16px;
        cursor: pointer;
        transition: color 0.13s ease, border-color 0.13s ease;
      }
      .xbtn:hover { color: var(--primary-text-color); border-color: var(--bmw-charge); }
      .xbtn ha-icon { --mdc-icon-size: 15px; }
      @media (max-width: 420px) {
        /* the labels are the first thing worth losing on a phone */
        .xbtn { font-size: 0; gap: 0; padding: 6px; }
      }
      .list { display: flex; flex-direction: column; padding: 6px 8px 10px; }
      .item {
        display: flex; align-items: center; justify-content: space-between; gap: 12px;
        padding: 11px 12px;
        border-radius: 10px;
        transition: background 0.13s ease;
      }
      .item:hover { background: var(--secondary-background-color); }
      .item + .item { border-top: 1px solid var(--divider-color); }
      .item:hover { border-top-color: transparent; }
      .item__name { color: var(--primary-text-color); font-size: 0.92rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .item__val {
        color: var(--secondary-text-color);
        font-size: 0.92rem; font-weight: 600;
        font-variant-numeric: tabular-nums;
        flex: 0 0 auto; text-align: right;
      }
      .list__head {
        padding: 12px 20px 0;
        color: var(--secondary-text-color);
        font-size: 0.78rem; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
      }
      .item--msg { justify-content: flex-start; align-items: flex-start; text-align: left; }
      .item__icon { --mdc-icon-size: 20px; color: var(--warning-color, #ffa600); flex: 0 0 auto; }
      .item__body { display: flex; flex-direction: column; gap: 2px; flex: 1 1 auto; min-width: 0; }
      .item__text { color: var(--primary-text-color); font-size: 0.92rem; }
      .item__sub { color: var(--secondary-text-color); font-size: 0.82rem; }
      .item__clamp {
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
      }
      .cc { border-radius: 10px; }
      .cc + .cc { border-top: 1px solid var(--divider-color); }
      .cc.is-open { background: var(--secondary-background-color); border-top-color: transparent; }
      .cc > .item--msg {
        width: 100%; background: none; border: none; color: inherit; font: inherit; cursor: pointer;
      }
      .cc.is-open > .item--msg:hover { background: transparent; }
      .cc--past .item__icon { color: var(--secondary-text-color); }
      .cc--past .item__text { color: var(--secondary-text-color); }
      .cc-earlier {
        display: flex; align-items: center; gap: 6px; width: 100%; padding: 4px 20px 10px;
        background: none; border: none; cursor: pointer; font: inherit; text-align: left;
        color: var(--secondary-text-color); font-size: 0.85rem;
      }
      .cc-earlier ha-icon { --mdc-icon-size: 18px; }
      .cc-earlier:hover { color: var(--primary-text-color); }
      .cc__text { margin: 0 0 8px; color: var(--primary-text-color); font-size: 0.86rem; line-height: 1.4; }
      .empty, .msg__body { color: var(--secondary-text-color); font-size: 0.9rem; }
      .empty { padding: 22px 18px; }
      .empty.empty--inline { padding: 8px 20px 14px; }

      /* message state */
      .msg { padding: 28px 20px; text-align: center; }
      .msg ha-icon { --mdc-icon-size: 40px; color: var(--disabled-text-color); }
      .msg__title { margin-top: 10px; font-weight: 600; color: var(--primary-text-color); }
      .msg__body { margin-top: 6px; }
      .msg code, .empty b { font-family: var(--code-font-family, monospace); }

      /* tire diagram */
      .tstat {
        margin-left: auto;
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 0.74rem; font-weight: 600;
        color: var(--c);
        white-space: nowrap;
      }
      .tstat__dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c); }
      /* Pressure and wear summarised across every wheel, above the diagram. */
      .tsum { display: flex; gap: 8px; padding: 12px 14px 2px; }
      .tsum__cell {
        flex: 1; min-width: 0;
        display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .tsum__k {
        font-size: 0.62rem; letter-spacing: 0.08em; text-transform: uppercase;
        color: var(--secondary-text-color);
      }
      .tsum__v {
        display: flex; gap: 6px;
        /* baseline, not center: a long pressure range wraps to two lines in a
           narrow column and the dot belongs on the first one. */
        align-items: baseline;
        font-size: 0.92rem; font-weight: 600; color: var(--c);
      }
      .tsum__v .tstat__dot { flex-shrink: 0; }
      .tsum__sub { font-size: 0.68rem; color: var(--secondary-text-color); }
      /* Grid, not absolute positioning: each wheel's own column sizes to the
         card so long fitment lines wrap instead of being clipped. */
      .tirewrap { container-type: inline-size; }
      .tirecar {
        display: grid;
        grid-template-columns: minmax(0, 1fr) clamp(72px, 24%, 118px) minmax(0, 1fr);
        grid-template-areas:
          ".  front ."
          "fl car   fr"
          "rl car   rr";
        column-gap: 10px; row-gap: 14px;
        padding: 10px 12px 16px;
        align-items: start;
      }
      .tirecar__front {
        grid-area: front;
        text-align: center;
        font-size: 0.56rem; letter-spacing: 0.18em; font-weight: 700;
        color: var(--secondary-text-color);
      }
      .tirecar__svg { grid-area: car; align-self: center; }
      .carsvg {
        display: block;
        width: 100%;
        height: auto;
        margin: 0 auto;
        overflow: visible;
        --tire: #1d2228;
      }
      /* The car is a fixed light-grey picture in both themes; strokes are in
         drawing units (948 wide), so ~6 units is one pixel on a card. */
      .carsvg__body, .carsvg__mirror { fill: url(#carBody); stroke: #a3abb4; stroke-width: 4; }
      .carsvg__hood { fill: url(#carBody); stroke: #b4bbc3; stroke-width: 3; }
      .carsvg__shut { fill: none; stroke: #b0b7bf; stroke-width: 3; }
      .carsvg__crease { fill: none; stroke: #f7f9fb; stroke-width: 4; opacity: 0.9; }
      .carsvg__roof { fill: url(#carRoof); stroke: #858c95; stroke-width: 3; }
      .carsvg__glass { fill: url(#carGlass); stroke: #1a2128; stroke-width: 6; stroke-linejoin: round; }
      .carsvg__frame { stroke-width: 10; }
      .carsvg__sheen { fill: #ffffff; opacity: 0.055; }
      .carsvg__pillar { fill: #232a31; }
      .carsvg__light { fill: #2a323b; }
      .carsvg__drl { fill: none; stroke: #ffffff; stroke-width: 4; stroke-linecap: round; }
      .carsvg__tail { fill: url(#carTail); stroke: #5a0f0b; stroke-width: 2.5; }
      .carsvg__tailcore { fill: none; stroke: #ffb199; stroke-width: 3.5; stroke-linecap: round; opacity: 0.9; }
      .carsvg__chrome { fill: #e9edf0; stroke: #8f979f; stroke-width: 2.5; }
      .carsvg__kidney { fill: #262c33; }
      .carsvg__kbar { fill: none; stroke: #59616a; stroke-width: 3; }
      .carsvg__handle { fill: #d6dbe0; stroke: #9aa2aa; stroke-width: 2; }
      .carsvg__rail { fill: none; stroke: #4c535b; stroke-width: 12; stroke-linecap: round; }
      .carsvg__rail--bar { stroke: #262c33; stroke-width: 8; stroke-linecap: butt; }
      .carsvg__wheel { fill: var(--tire); stroke: var(--tire); stroke-width: 14; stroke-linejoin: round; }
      .carsvg__wheel--lit { filter: url(#carGlow); }

      /* closures / security diagram */
      .closcar { padding: 10px 12px 4px; display: flex; justify-content: center; }
      .closcar .carsvg { width: 50%; min-width: 138px; max-width: 196px; margin: 0; }
      .cldiag__hit { fill: transparent; cursor: pointer; }
      .cldiag__hit:hover { fill: rgba(127, 127, 127, 0.14); }
      .cldiag__lit { pointer-events: none; }
      .cldiag__zone {
        fill: var(--c); fill-opacity: 0.5;
        stroke: var(--c); stroke-width: 14; stroke-linejoin: round;
        filter: url(#carGlow);
      }
      .cldiag__core { fill: none; stroke: rgba(255, 255, 255, 0.55); stroke-width: 3.5; stroke-linejoin: round; }
      .cldiag__drop { fill: none; stroke: var(--c); stroke-width: 10; }
      .cldiag__sill { fill: #a2a9b1; }
      .cldiag__sillshadow { fill: none; stroke: #3b434c; stroke-width: 5; opacity: 0.55; }
      .cldiag__door { fill: var(--c); stroke: var(--c); stroke-width: 8; stroke-linejoin: round; filter: url(#carGlow); }
      .cldiag__doortrim { fill: rgba(0, 0, 0, 0.45); }
      .cldiag__dooredge { fill: none; stroke: rgba(255, 255, 255, 0.5); stroke-width: 4; stroke-linecap: round; }
      .cldiag__lock { cursor: pointer; }
      .cldiag__lockbody { fill: var(--c); }
      .cldiag__shackle { fill: none; stroke: var(--c); stroke-width: 16; stroke-linecap: round; }
      .item__dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }

      .wlabel {
        min-width: 0;
        display: flex; flex-direction: column; gap: 1px;
        padding: 4px 4px;
        border-radius: 10px;
        transition: background 0.14s ease;
      }
      .wlabel:hover { background: var(--secondary-background-color); }
      .wlabel--fl { grid-area: fl; align-items: flex-end; text-align: right; }
      .wlabel--fr { grid-area: fr; align-items: flex-start; text-align: left; }
      .wlabel--rl { grid-area: rl; align-items: flex-end; text-align: right; }
      .wlabel--rr { grid-area: rr; align-items: flex-start; text-align: left; }
      .wlabel__pos {
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 0.66rem; letter-spacing: 0.05em; text-transform: uppercase;
        color: var(--secondary-text-color);
      }
      .wlabel--fl .wlabel__pos, .wlabel--rl .wlabel__pos { flex-direction: row-reverse; }
      .wlabel__badge { color: var(--c); font-weight: 700; }
      .wlabel__val {
        font-size: 1.45rem; font-weight: 600; line-height: 1.1;
        font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
        white-space: nowrap;
      }
      .wlabel__val i {
        font-size: 0.66rem; font-weight: 500; font-style: normal;
        color: var(--secondary-text-color); margin-left: 2px;
      }
      .wlabel__sub {
        font-size: 0.66rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      /* Per-wheel fitment. Wraps rather than clips -- a tyre size and a tread
         name are long, and the column is whatever the dashboard gives us. */
      .wlabel__meta {
        font-size: 0.63rem; line-height: 1.35;
        color: var(--secondary-text-color);
        overflow-wrap: anywhere;
      }
      .wlabel__val + .wlabel__meta,
      .wlabel__sub + .wlabel__meta { margin-top: 3px; }
      .wlabel__due { color: var(--primary-text-color); font-weight: 600; }
      /* Narrow columns: the diagram is the first thing worth losing, and the
         four wheels fall back to a 2x2 grid that still reads front-over-rear.
         Must come after the .wlabel rules it overrides. */
      @container (max-width: 340px) {
        .tirecar {
          grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
          grid-template-areas:
            "front front"
            "fl    fr"
            "rl    rr";
        }
        .tirecar__svg { display: none; }
        .wlabel--fl, .wlabel--rl { align-items: flex-start; text-align: left; }
        .wlabel--fl .wlabel__pos, .wlabel--rl .wlabel__pos { flex-direction: row; }
      }

      /* ---- charging history ---- */
      .chg__summary {
        display: flex; gap: 8px; padding: 10px 14px 4px;
      }
      .chg__stat {
        flex: 1; display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .chg__stat-val {
        font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums;
      }
      .chg__stat-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .chg__list { padding: 6px 6px 8px; }
      .chg__session { border-radius: 12px; }
      .chg__session.is-open { background: var(--secondary-background-color); }
      .chg__row {
        width: 100%; display: flex; align-items: center; justify-content: space-between;
        gap: 10px; padding: 10px 10px; background: none; border: none;
        color: inherit; text-align: left; cursor: pointer; border-radius: 12px;
      }
      .chg__row:hover { background: var(--secondary-background-color); }
      .chg__session.is-open .chg__row:hover { background: transparent; }
      .chg__row-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
      .chg__date { font-weight: 600; font-size: 0.92rem; }
      .chg__meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .chg__soc {
        font-size: 0.72rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .chg__figures {
        display: flex; flex-direction: column; align-items: flex-end; gap: 3px;
        white-space: nowrap;
      }
      .chg__energy { font-variant-numeric: tabular-nums; font-size: 0.86rem; }
      .chg__energy--lead { font-size: 0.98rem; font-weight: 600; }
      .chg__cost {
        font-weight: 600; font-variant-numeric: tabular-nums;
      }
      .chg__cost--none {
        font-weight: 400; font-size: 0.72rem; color: var(--secondary-text-color);
      }
      .chg__badge, .chg__tag {
        font-size: 0.66rem; padding: 1px 7px; border-radius: 999px;
        border: 1px solid var(--divider-color); color: var(--secondary-text-color);
        white-space: nowrap;
      }
      .chg__badge--home { border-color: var(--bmw-high); color: var(--bmw-high); }
      .chg__badge--away { border-color: var(--bmw-charge); color: var(--bmw-charge); }
      .chg__badge--assumed { border-style: dashed; }
      /* Sessions | Places */
      .ctabs { display: flex; gap: 6px; padding: 10px 14px 0; }
      .ctabs__btn {
        flex: 1; padding: 6px 10px;
        font: inherit; font-size: 0.8rem; font-weight: 600;
        color: var(--secondary-text-color); background: transparent;
        border: 1px solid var(--divider-color); border-radius: 999px; cursor: pointer;
      }
      .ctabs__btn.is-active {
        color: var(--bmw-charge); border-color: var(--bmw-charge);
        background: var(--secondary-background-color);
      }
      .pl__map { padding: 10px 14px 0; }
      .pl__map ha-map { width: 100%; border-radius: 10px; overflow: hidden; }
      .pl__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .chg__tag--warn { border-color: var(--bmw-mid); color: var(--bmw-mid); }
      .chg__tag--sun { border-color: var(--bmw-high); color: var(--bmw-high); }
      .chg__detail { padding: 2px 12px 12px; }
      .chg__chart {
        width: 100%; height: 64px; display: block; margin-bottom: 8px;
      }
      .chg__chart-line {
        fill: none; stroke: var(--bmw-charge); stroke-width: 2;
        stroke-linejoin: round; stroke-linecap: round;
        vector-effect: non-scaling-stroke;
      }
      .chg__chart-fill { fill: var(--bmw-charge); opacity: 0.12; stroke: none; }
      .chg__chart-max {
        fill: var(--secondary-text-color); font-size: 9px;
      }
      .chg__facts { display: flex; flex-wrap: wrap; gap: 6px; }
      .chg__fact {
        flex: 1 1 40%; display: flex; justify-content: space-between; gap: 8px;
        padding: 6px 10px; border-radius: 10px;
        background: var(--card-background-color);
      }
      .chg__fact-lbl { color: var(--secondary-text-color); font-size: 0.76rem; }
      .chg__fact-val { font-variant-numeric: tabular-nums; font-size: 0.82rem; }

      /* ---- battery health ---- */
      .bh { padding: 6px 14px 14px; display: flex; flex-direction: column; gap: 14px; }
      .bh__hero { display: flex; align-items: center; gap: 16px; }
      .bh__ring { width: 92px; height: 92px; flex: 0 0 auto; }
      .bh__ring-track { fill: none; stroke: var(--divider-color); stroke-width: 7; }
      .bh__ring-val {
        fill: none; stroke: var(--bmw-high); stroke-width: 7; stroke-linecap: round;
        transition: stroke-dasharray 0.6s ease;
      }
      .bh__ring-text {
        fill: var(--primary-text-color); font-size: 17px; font-weight: 600;
        text-anchor: middle; font-variant-numeric: tabular-nums;
      }
      .bh__hero-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      .bh__usable {
        font-size: 1.9rem; font-weight: 600; line-height: 1;
        font-variant-numeric: tabular-nums;
      }
      .bh__usable i { font-size: 0.9rem; font-weight: 500; font-style: normal;
        color: var(--secondary-text-color); }
      .bh__usable-lbl {
        font-size: 0.7rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .bh__vsnew { font-size: 0.82rem; color: var(--bmw-high); font-weight: 500; }
      .bh__learn { display: flex; flex-direction: column; gap: 8px; padding: 10px 0; }
      .bh__learn-val {
        font-size: 1.35rem; font-weight: 600; font-variant-numeric: tabular-nums;
      }
      .bh__bar {
        height: 7px; border-radius: 999px; background: var(--divider-color);
        overflow: hidden;
      }
      .bh__bar-fill {
        height: 100%; border-radius: 999px; background: var(--bmw-charge);
        transition: width 0.6s ease;
      }
      .bh__hint { font-size: 0.78rem; color: var(--secondary-text-color); line-height: 1.35; }
      .bh__trend { display: flex; flex-direction: column; gap: 4px; }
      .bh__trend-title {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .bh__chart { width: 100%; height: 70px; display: block; }

      /* ---- efficiency & real range ---- */
      .ef { padding: 6px 14px 14px; display: flex; flex-direction: column; gap: 14px; }
      .ef__hero { display: flex; flex-direction: column; gap: 4px; }
      .ef__hero-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      .ef__hero-val {
        font-size: 2.1rem; font-weight: 600; line-height: 1;
        font-variant-numeric: tabular-nums;
      }
      .ef__hero-val i {
        font-size: 0.95rem; font-weight: 500; font-style: normal;
        color: var(--secondary-text-color);
      }
      .ef__hero-lbl {
        font-size: 0.7rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .ef__vs { font-size: 0.82rem; color: var(--secondary-text-color); font-weight: 500; }
      .ef__vs--over { color: var(--bmw-high); }
      .ef__vs--under { color: var(--bmw-mid); }
      .ef__facts { display: flex; flex-wrap: wrap; gap: 8px; }
      .ef__fact {
        flex: 1 1 44%; display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .ef__fact-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .ef__fact-val { font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; }
      .ef__fact-note { font-size: 0.72rem; color: var(--secondary-text-color); }
      .ef__trend { display: flex; flex-direction: column; gap: 4px; }
      .ef__trend-title {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .ef__chart { width: 100%; height: 88px; display: block; }
      .ef__bar { fill: var(--bmw-charge); opacity: 0.85; }
      .ef__bar-lbl { fill: var(--secondary-text-color); font-size: 8px; }
      .ef__note { font-size: 0.72rem; color: var(--secondary-text-color); line-height: 1.35; }
      .mi__allowance { display: flex; flex-direction: column; gap: 6px; }
      .mi__bar {
        position: relative; height: 10px; border-radius: 5px; overflow: hidden;
        background: var(--divider-color, rgba(127, 127, 127, 0.25));
      }
      .mi__fill { position: absolute; inset: 0 auto 0 0; background: var(--bmw-charge); }
      .mi__bar--over .mi__fill { background: var(--bmw-high); }
      .mi__plan {
        position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px;
        background: var(--primary-text-color); opacity: 0.6;
      }
      .mi__of { font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; }
      .mi__bar-partial { opacity: 0.45; }

      /* trips */
      .tr__review { padding: 8px 14px 4px; display: flex; flex-direction: column; gap: 12px; }
      .tr__tiles { display: flex; gap: 8px; flex-wrap: wrap; }
      .tr__tile {
        flex: 1 1 40%; display: flex; flex-direction: column; gap: 2px;
        padding: 8px 10px; border-radius: 12px;
        background: var(--secondary-background-color);
      }
      .tr__tile-val { font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; }
      .tr__tile-val i { font-style: normal; font-size: 0.72rem; color: var(--secondary-text-color); }
      .tr__tile-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      /* The secondary reading under a headline figure (battery-side consumption
         beneath the plug-side one). Deliberately quieter than the label. */
      .tr__tile-sub {
        font-size: 0.68rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums; opacity: 0.85;
      }
      .tr__delta { font-size: 0.7rem; font-variant-numeric: tabular-nums; }
      .tr__delta--up { color: var(--bmw-charge, #34c759); }
      .tr__delta--down { color: var(--error-color, #ff453a); }
      .tr__split { display: flex; flex-direction: column; gap: 6px; }
      .tr__bar {
        display: flex; height: 12px; border-radius: 999px; overflow: hidden;
        background: var(--secondary-background-color);
      }
      .tr__seg { display: block; height: 100%; }
      .tr__seg--business, .tr__dot.tr__seg--business { background: #0066b1; }
      .tr__seg--commute, .tr__dot.tr__seg--commute { background: #00a1e0; }
      .tr__seg--private, .tr__dot.tr__seg--private { background: #7ac142; }
      .tr__seg--unc, .tr__dot.tr__seg--unc { background: var(--disabled-text-color, #8a8a8a); }
      .tr__legend { display: flex; flex-wrap: wrap; gap: 10px; }
      .tr__leg {
        display: inline-flex; align-items: center; gap: 5px;
        font-size: 0.72rem; color: var(--secondary-text-color);
        font-variant-numeric: tabular-nums;
      }
      .tr__dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }

      /* ---- trip map ---- */
      .map__filters {
        display: flex; gap: 6px; padding: 4px 14px 10px; flex-wrap: wrap;
      }
      .map__chip {
        border: 1px solid var(--divider-color); background: transparent;
        color: var(--secondary-text-color); cursor: pointer;
        font: inherit; font-size: 0.74rem; padding: 3px 12px; border-radius: 999px;
      }
      .map__chip:hover { border-color: var(--primary-color); }
      .map__chip.is-active {
        background: var(--primary-color); color: var(--text-primary-color, #fff);
        border-color: var(--primary-color);
      }
      .map__holder {
        margin: 0 14px; border-radius: 12px; overflow: hidden;
        border: 1px solid var(--divider-color);
      }
      .map__holder ha-map { width: 100%; }
      .tr__minimap {
        margin: 2px 0 12px; height: 200px; border-radius: 10px; overflow: hidden;
        border: 1px solid var(--divider-color); background: var(--card-background-color);
      }
      .tr__minimap ha-map { width: 100%; }
      .tr__style { display: flex; flex-direction: column; gap: 6px; }
      .tr__style-head { display: flex; align-items: center; justify-content: space-between; }
      .tr__style-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .tr__stars { font-size: 0.95rem; color: #f5a623; letter-spacing: 1px; }
      .tr__dests { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
      .tr__dests-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em; width: 100%;
      }
      .tr__dest {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 4px 8px; border-radius: 999px;
        background: var(--secondary-background-color); font-size: 0.78rem;
      }
      .tr__dest-n { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .tr__badge {
        font-size: 0.66rem; padding: 1px 7px; border-radius: 999px;
        color: #fff; text-transform: uppercase; letter-spacing: 0.03em;
      }
      .tr__badge--business { background: #0066b1; }
      .tr__badge--commute { background: #00a1e0; }
      .tr__badge--private { background: #7ac142; }
      /* A drive under way: the badge that replaces a classification until the
         trip lands and there is something to classify. */
      .tr__badge--live {
        background: var(--bmw-charge, #4cc2ff);
        display: inline-flex; align-items: center; gap: 5px;
      }
      .tr__live-dot {
        width: 6px; height: 6px; border-radius: 50%; background: #fff;
        animation: bd-trip-pulse 2s ease-in-out infinite;
      }
      @media (prefers-reduced-motion: reduce) {
        .tr__live-dot { animation: none; }
      }
      /* The in-progress row leads with an accent edge so it reads as different
         from the recorded trips below it without shouting. */
      .chg__session.is-live > .chg__row {
        border-left: 3px solid var(--bmw-charge, #4cc2ff);
        padding-left: 7px;
      }
      .tr__live-note {
        font-size: 0.72rem; color: var(--secondary-text-color);
        line-height: 1.45; padding: 8px 2px 2px;
      }
      .tr__auto { font-style: normal; opacity: 0.75; text-transform: none; }
      .tr__classify { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 8px 2px 2px; }
      .tr__classify-lbl {
        font-size: 0.68rem; color: var(--secondary-text-color);
        text-transform: uppercase; letter-spacing: 0.04em;
      }
      .tr__cls-btns { display: inline-flex; gap: 6px; flex-wrap: wrap; }
      .tr__cls-btn {
        border: 1px solid var(--divider-color); background: none; cursor: pointer;
        font-size: 0.66rem; padding: 3px 9px; border-radius: 999px;
        color: var(--primary-text-color); text-transform: uppercase; letter-spacing: 0.03em;
        opacity: 0.6;
      }
      .tr__cls-btn:hover { opacity: 1; }
      .tr__cls-btn.is-active { opacity: 1; color: #fff; border-color: transparent; }

      @media (prefers-reduced-motion: reduce) {
        .dot--live { animation: none; }
        .gauge__ring { transition: none; }
      }
      @media (max-width: 360px) {
        .band { flex-direction: column; align-items: stretch; }
        .gauge { align-self: center; }
      }
    </style>`;
  }
};
