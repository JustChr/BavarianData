// ESLint for the bundled Lovelace card.
//
// Dev-only: the card in custom_components/bavariandata/www/ still ships as
// dependency-free vanilla JS with no build step, and nothing from node_modules
// reaches a user.
//
// The rule selection follows the same philosophy as pyproject.toml's ruff
// config: real bug patterns are enforced, cosmetic style is not. The one rule
// worth the whole setup is no-unsanitized/property -- the card builds its UI by
// assigning template strings to innerHTML, so an unescaped value from a vehicle
// name, a BMW message or a user's YAML is an XSS hole in someone's dashboard.
// tests/test_card_escaping.py has guarded that with a hand-written JS tokenizer
// in Python; this does the same job with a real parser.

import js from "@eslint/js";
import nounsanitized from "eslint-plugin-no-unsanitized";

export default [
  {
    // The generated/vendored world is not ours to lint.
    ignores: ["node_modules/**", ".venv/**"],
  },
  {
    files: ["custom_components/bavariandata/www/*.js"],
    plugins: { "no-unsanitized": nounsanitized },
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "script",
      globals: {
        // Browser surface the card actually touches.
        window: "readonly",
        document: "readonly",
        navigator: "readonly",
        location: "readonly",
        console: "readonly",
        fetch: "readonly",
        Blob: "readonly",
        URL: "readonly",
        HTMLElement: "readonly",
        customElements: "readonly",
        CustomEvent: "readonly",
        Event: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        requestAnimationFrame: "readonly",
        localStorage: "readonly",
        getComputedStyle: "readonly",
        ResizeObserver: "readonly",
        IntersectionObserver: "readonly",
        MutationObserver: "readonly",
        Intl: "readonly",
        // Leaflet, loaded by Home Assistant's own map card.
        L: "readonly",
      },
    },
    rules: {
      // Spread, not replaced: setting `rules` after `...js.configs.recommended`
      // silently drops every recommended rule.
      ...js.configs.recommended.rules,

      // `no-unsanitized/property` is deliberately OFF for this file, and that
      // is the opposite of what it was added for -- so, the reason:
      //
      // The card renders by assigning a large template literal to innerHTML.
      // The rule flags such an assignment unless *every* interpolation is a
      // literal or a recognised escaper, and these templates legitimately
      // interpolate numbers, translation lookups and pre-escaped fragments as
      // well as `_esc()` calls. `escape.methods` does not help: one `${count}`
      // is enough to flag a hundred-line template. It reported all twelve
      // render methods, none of which is a hole.
      //
      // tests/test_card_escaping.py does this job properly instead: it tokenises
      // the source and checks each interpolation individually against the
      // escaping helpers and a taint analysis. Keep that test; it is the real
      // guard. What is kept here is the *method* rule, which catches
      // insertAdjacentHTML/document.write and has no such false-positive mode.
      "no-unsanitized/property": "off",
      "no-unsanitized/method": "error",

      // The card logs a version banner on load, which is deliberate and carries
      // its own disable comment; everything else should stay quiet.
      "no-console": "warn",

      // `catch (e) {}` with an unread binding is idiomatic here, and a leading
      // underscore is how this file already says "required, deliberately
      // unused". Everything else unused is a real finding.
      "no-unused-vars": [
        "error",
        {
          caughtErrors: "none",
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
    },
  },
];
