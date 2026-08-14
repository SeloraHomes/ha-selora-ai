import en from "../../../translations/en.json";
import fr from "../../../translations/fr.json";
import de from "../../../translations/de.json";
import es from "../../../translations/es.json";
import it from "../../../translations/it.json";
import nl from "../../../translations/nl.json";
import hu from "../../../translations/hu.json";
import pt from "../../../translations/pt.json";
import ru from "../../../translations/ru.json";
import ja from "../../../translations/ja.json";
import ko from "../../../translations/ko.json";
import zhHans from "../../../translations/zh-Hans.json";
import zhHant from "../../../translations/zh-Hant.json";

const CATALOG = {
  en,
  fr,
  de,
  es,
  it,
  nl,
  hu,
  pt,
  ru,
  ja,
  ko,
  "zh-hans": zhHans,
  "zh-hant": zhHant,
};

export function pickLocale(hass) {
  const raw = hass?.language || hass?.locale?.language || "en";
  const lower = String(raw).toLowerCase();
  if (CATALOG[lower]) return lower;
  const base = lower.split("-")[0];
  return CATALOG[base] ? base : "en";
}

// A counted phrase has to pick its noun by the TARGET language's plural
// categories, which are not English's two. Russian chooses between день / дня /
// дней on the last digits — 1 and 21 take one form, 2-4 and 22-24 a second, 5-20
// and 11-14 a third — so a single static plural renders "2 дней" and "21 дней".
// German needs a separate form for a different reason: the duration is read
// inside "ist offline seit …", and `seit` governs the dative, so the plural is
// "2 Tagen" rather than "2 Tage".
//
// Keys are `<base>_<category>`, where the category comes from Intl.PluralRules
// for the resolved locale. Every locale carries the full set and a language that
// makes no distinction simply repeats one string across it, so no caller and no
// lookup has to know which languages differ. `<base>_other` is the fallback
// bucket for a category a locale happens to omit.
//
// The count is interpolated from inside the resolved string via {count} rather
// than concatenated by the caller — the separator belongs to the language too
// ("2 days" against "2日"), and a caller joining with a space cannot know that.
export function localizePlural(hass, base, count, fallback) {
  let category = "other";
  try {
    category = new Intl.PluralRules(pickLocale(hass)).select(count);
  } catch {
    // An unresolvable locale tag throws; `other` is the safe bucket.
  }
  const phrase =
    localize(hass, `${base}_${category}`, null) ??
    localize(hass, `${base}_other`, null) ??
    fallback;
  return String(phrase).replace("{count}", String(count));
}

export function localize(hass, key, fallback) {
  const lang = pickLocale(hass);
  return CATALOG[lang]?.common?.[key] ?? CATALOG.en?.common?.[key] ?? fallback;
}
