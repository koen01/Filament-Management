/* i18n – lightweight German / English translations */

const I18N = {
  de: {
    // Page
    'page.title':            'Filament Anzeige (K2 Plus / CFS)',
    'header.title':          'Filament Anzeige',

    // Status tags
    'status.empty':          'leer',
    'status.active':         'aktiv',
    'status.ready':          'bereit',

    // Section titles
    'section.active':        'Aktiv',
    'section.history':       'Historie pro Slot',
    'section.history_last4': 'letzte 4',
    'section.moon_summary':  'Moonraker-History (gesamt)',

    // Refresh control
    'refresh.title':         'Update-Intervall',
    'refresh.toggle_title':  'Auto-Update an/aus',

    // Spool modal
    'modal.close':           'Schließen',
    'modal.weigh_label':     'Istgewicht (g)',
    'modal.weigh_ph':        'z.B. 206',
    'modal.btn_apply':       'Übernehmen',
    'modal.newroll_label':   'Neue Rolle (g)',
    'modal.newroll_ph':      'z.B. 1000',
    'modal.btn_rollchange':  'Rollwechsel',
    'modal.hint':            'Hinweis: Das speichert nur lokal in dieser App (kein POST an den Drucker). Rollwechsel versteckt alte Drucke in der Slot-Historie (bleibt intern gespeichert).',

    // Spool stats
    'spool.stats_full':      'Rest (berechnet): {remaining} · verbraucht seit Übernahme: {used} · Gesamt (Slot): {total}',
    'spool.stats_partial':   'Rest (aktuell): {remaining} · Tipp: "Istgewicht" eintragen und Übernehmen.',
    'spool.stats_none':      'Noch kein Referenzwert. Trage "Istgewicht" ein und klicke Übernehmen.',

    // Moonraker history
    'moon.empty':            'Keine Moonraker-History Daten',
    'moon.no_consumption':   'Kein Verbrauch in History gefunden',

    // History
    'history.no_name':       '(ohne name)',
    'history.active_suffix': ' · aktiv',
    'history.no_data':       'Noch keine Daten',

    // Assignment
    'assign.title_existing': 'Zuordnung (lokal gespeichert)',
    'assign.title_new':      'Zu Slot zuordnen (lokal)',
    'assign.btn_edit':       'Ändern',
    'assign.select_default': '— Slot wählen —',
    'assign.total':          'gesamt',
    'assign.btn_update':     'Zuordnung aktualisieren',
    'assign.btn_assign':     'Zuordnen',
    'assign.alert_select':   'Bitte mindestens einen Slot wählen.',
    'assign.error_save':     'Konnte nicht speichern: ',
    'assign.current':        'Aktuell: ',

    // Badges
    'badge.printer_ok':      'Printer: verbunden',
    'badge.printer_off':     'Printer: getrennt',
    'badge.printer_dash':    'Printer: —',
    'badge.cfs_ok':          'CFS: erkannt · {ts}',
    'badge.cfs_off':         'CFS: —',

    // Footer
    'footer.tip':            'Tip: Wenn Farben/Material nicht angezeigt werden, prüfe in <code>data/config.json</code> die <code>moonraker_url</code>.',

    // Language
    'lang.de':               'DE',
    'lang.en':               'EN',
  },

  en: {
    // Page
    'page.title':            'Filament Display (K2 Plus / CFS)',
    'header.title':          'Filament Display',

    // Status tags
    'status.empty':          'empty',
    'status.active':         'active',
    'status.ready':          'ready',

    // Section titles
    'section.active':        'Active',
    'section.history':       'History per Slot',
    'section.history_last4': 'last 4',
    'section.moon_summary':  'Moonraker History (total)',

    // Refresh control
    'refresh.title':         'Refresh interval',
    'refresh.toggle_title':  'Auto-refresh on/off',

    // Spool modal
    'modal.close':           'Close',
    'modal.weigh_label':     'Current weight (g)',
    'modal.weigh_ph':        'e.g. 206',
    'modal.btn_apply':       'Apply',
    'modal.newroll_label':   'New roll (g)',
    'modal.newroll_ph':      'e.g. 1000',
    'modal.btn_rollchange':  'Roll change',
    'modal.hint':            'Note: This saves locally in this app only (no POST to printer). Roll change hides old prints in slot history (kept internally).',

    // Spool stats
    'spool.stats_full':      'Remaining (calc): {remaining} · used since reference: {used} · Total (slot): {total}',
    'spool.stats_partial':   'Remaining (current): {remaining} · Tip: enter "Current weight" and click Apply.',
    'spool.stats_none':      'No reference yet. Enter "Current weight" and click Apply.',

    // Moonraker history
    'moon.empty':            'No Moonraker history data',
    'moon.no_consumption':   'No consumption found in history',

    // History
    'history.no_name':       '(unnamed)',
    'history.active_suffix': ' · active',
    'history.no_data':       'No data yet',

    // Assignment
    'assign.title_existing': 'Assignment (saved locally)',
    'assign.title_new':      'Assign to slot (local)',
    'assign.btn_edit':       'Edit',
    'assign.select_default': '— Pick slot —',
    'assign.total':          'total',
    'assign.btn_update':     'Update assignment',
    'assign.btn_assign':     'Assign',
    'assign.alert_select':   'Please select at least one slot.',
    'assign.error_save':     'Could not save: ',
    'assign.current':        'Current: ',

    // Badges
    'badge.printer_ok':      'Printer: connected',
    'badge.printer_off':     'Printer: disconnected',
    'badge.printer_dash':    'Printer: —',
    'badge.cfs_ok':          'CFS: detected · {ts}',
    'badge.cfs_off':         'CFS: —',

    // Footer
    'footer.tip':            'Tip: If colors/material are not shown, check <code>moonraker_url</code> in <code>data/config.json</code>.',

    // Language
    'lang.de':               'DE',
    'lang.en':               'EN',
  }
};

let _i18nLang = 'en';

/**
 * Translate a key, optionally replacing {placeholder} tokens.
 * Falls back to English, then returns the key itself.
 */
function t(key, params) {
  let s = (I18N[_i18nLang] && I18N[_i18nLang][key]) || (I18N.en && I18N.en[key]) || key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), v);
    }
  }
  return s;
}

/** Detect preferred language: localStorage → navigator → fallback 'en' */
function i18nDetectLang() {
  const stored = localStorage.getItem('lang');
  if (stored === 'de' || stored === 'en') return stored;
  const nav = (navigator.languages || [navigator.language || '']);
  for (const l of nav) {
    if (typeof l === 'string' && l.toLowerCase().startsWith('de')) return 'de';
  }
  return 'en';
}

/** Set the active language, persist, and re-translate the DOM. */
function i18nSetLang(lang) {
  _i18nLang = (lang === 'de') ? 'de' : 'en';
  localStorage.setItem('lang', _i18nLang);
  document.documentElement.lang = _i18nLang;
  document.title = t('page.title');
  i18nTranslateDOM();
}

/** Translate static elements that carry data-i18n* attributes. */
function i18nTranslateDOM() {
  for (const el of document.querySelectorAll('[data-i18n]')) {
    el.textContent = t(el.dataset.i18n);
  }
  for (const el of document.querySelectorAll('[data-i18n-html]')) {
    el.innerHTML = t(el.dataset.i18nHtml);
  }
  for (const el of document.querySelectorAll('[data-i18n-placeholder]')) {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  }
  for (const el of document.querySelectorAll('[data-i18n-title]')) {
    el.title = t(el.dataset.i18nTitle);
  }
}

/** Return the current language code ('de' | 'en'). */
function i18nLang() { return _i18nLang; }

// Auto-detect on load
_i18nLang = i18nDetectLang();
