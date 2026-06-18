import en from "../../../translations/en.json";
import fr from "../../../translations/fr.json";
import de from "../../../translations/de.json";
import es from "../../../translations/es.json";
import it from "../../../translations/it.json";
import nl from "../../../translations/nl.json";
import hu from "../../../translations/hu.json";

const CATALOG = { en, fr, de, es, it, nl, hu };

export function pickLocale(hass) {
  const raw = hass?.language || hass?.locale?.language || "en";
  const base = String(raw).toLowerCase().split("-")[0];
  return CATALOG[base] ? base : "en";
}

export function localize(hass, key, fallback) {
  const lang = pickLocale(hass);
  return CATALOG[lang]?.common?.[key] ?? CATALOG.en?.common?.[key] ?? fallback;
}
