/* ------------------------------------------------------------------------- *
 * BavarianData card: its strings in British English (en-GB)                 *
 *                                                                           *
 * One file per language; the card loads English and the user's language     *
 * only, with its own ?v=. Anything missing falls back to English, key by    *
 * key. Same keys in the same order as the English file.                     *
 * ------------------------------------------------------------------------- */
// British English is a delta over `en`, not a table of its own: `t()` falls
// back to `TRANSLATIONS.en` key by key, so only the strings that actually
// differ belong here. `tests/test_translations_dialect.py` derives the
// expected contents from `tools/spelling_en_gb.json` and fails if this drifts
// from it -- add the word there, not a second full table here.
export default {
  cl_tire: "Tyre data",
  tires: "Tyres",
  no_tire_data:
    "No tyre data for this vehicle yet. Enable the Tyre data cluster and drive to populate readings.",
  check_tyres: "Check tyres",
  sh_sedan: "BMW saloon",
  fu_since: "Since refuelling",
  fu_note:
    "Read off the tank level, which the car reports to within a few litres, so each figure is an estimate. The average over several refuels is the one to trust.",
};
