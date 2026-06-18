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

export function localize(hass, key, fallback) {
  const lang = pickLocale(hass);
  return CATALOG[lang]?.common?.[key] ?? CATALOG.en?.common?.[key] ?? fallback;
}
