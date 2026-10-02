import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { en, zh } from "./translations";
import type { TranslationKey } from "./translations";

export type Language = "zh" | "en";
const storageKey = "research_language";

function translate(language: Language, key: TranslationKey, values?: Record<string, string | number>): string {
  const template = language === "en" ? en[key] : zh[key];
  return template.replace(/\{(\w+)\}/g, (match, name: string) => String(values?.[name] ?? match));
}

type LanguageContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: TranslationKey, values?: Record<string, string | number>) => string;
};

const LanguageContext = createContext<LanguageContextValue>({
  language: "zh", setLanguage: () => {}, t: (key, values) => translate("zh", key, values),
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(() => {
    try { return window.localStorage.getItem(storageKey) === "en" ? "en" : "zh"; }
    catch { return "zh"; }
  });
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    try { window.localStorage.setItem(storageKey, language); } catch { /* Storage may be disabled. */ }
  }, [language]);
  const value = useMemo<LanguageContextValue>(() => ({
    language, setLanguage, t: (key, values) => translate(language, key, values),
  }), [language]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export const useLanguage = () => useContext(LanguageContext);

export function LanguageSwitch() {
  const { language, setLanguage, t } = useLanguage();
  return <div className="language-switch" role="group" aria-label={t("language.label")}>
    <button type="button" aria-pressed={language === "zh"} onClick={() => setLanguage("zh")}>简体中文</button>
    <button type="button" aria-pressed={language === "en"} onClick={() => setLanguage("en")}>English</button>
  </div>;
}
