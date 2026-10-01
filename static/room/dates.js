// The room's one clock.
//
// "Today" is the writer's LOCAL calendar date. An air date is a calendar day
// the board wrote ("2026-10-05", or the first ten characters of a longer
// value), and it is compared as a day, never converted through UTC. The old
// page mixed the two and showed ON AIR on Sunday evening in the Americas; the
// room keeps every day a plain YYYY-MM-DD string and does its arithmetic on
// whole day numbers, so neither a time zone nor a daylight saving change can
// move a date.
(function installRoomDates(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomDates = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomDates() {
  const DAY_MS = 86400000;
  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  // The local calendar date of an instant, as YYYY-MM-DD.
  function localIso(date) {
    const d = date instanceof Date ? date : new Date(date);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function today(now) {
    return localIso(now === undefined ? new Date() : now);
  }

  // The calendar day an air date names, or '' when it names none.
  function calendarDay(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || '').trim());
    if (!match) return '';
    const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
    const check = new Date(Date.UTC(year, month - 1, day));
    if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) return '';
    return `${match[1]}-${match[2]}-${match[3]}`;
  }

  // A calendar day as a whole number of days, for arithmetic only.
  function dayNumber(iso) {
    const day = calendarDay(iso);
    if (!day) return NaN;
    const [y, m, d] = day.split('-').map(Number);
    return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
  }

  function fromDayNumber(n) {
    const date = new Date(n * DAY_MS);
    return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
  }

  function addDays(iso, days) {
    return fromDayNumber(dayNumber(iso) + days);
  }

  function daysBetween(from, to) {
    return dayNumber(to) - dayNumber(from);
  }

  // 0 = sunday, as Date#getDay.
  function weekday(iso) {
    return (((dayNumber(iso) + 4) % 7) + 7) % 7; // day 0 of the epoch was a thursday
  }

  const WEEKDAY_NAMES = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

  // A weekday name ("monday", as config.json and the settings form write it)
  // to weekday()'s convention (0 = sunday), or -1 for anything else.
  function weekdayNumber(name) {
    return WEEKDAY_NAMES.indexOf(String(name || '').trim().toLowerCase());
  }

  // The anchor day of the week `iso` falls in, for a week that starts on
  // `anchor` (0 = sunday .. 6 = saturday). Defaults to monday, the board's
  // long-standing week start, so every existing caller is unchanged.
  function weekAnchorOf(iso, anchor) {
    const start = typeof anchor === 'number' && anchor >= 0 && anchor <= 6 ? anchor : 1;
    return addDays(iso, -(((weekday(iso) - start) % 7 + 7) % 7));
  }

  // The monday of the week a day falls in. Weeks run monday to sunday.
  // A thin, permanent alias of weekAnchorOf(iso, 1): the board's publish day
  // is now configurable, but "mondayOf" stays as the historical default.
  function mondayOf(iso) {
    return weekAnchorOf(iso, 1);
  }

  function parts(iso) {
    const day = calendarDay(iso);
    if (!day) return null;
    const [y, m, d] = day.split('-').map(Number);
    return { year: y, month: m, day: d, weekday: weekday(day) };
  }

  // "mon oct 5"
  function formatDay(iso) {
    const p = parts(iso);
    return p ? `${WEEKDAYS[p.weekday]} ${MONTHS[p.month - 1]} ${p.day}` : '';
  }

  // "oct 5"
  function formatShort(iso) {
    const p = parts(iso);
    return p ? `${MONTHS[p.month - 1]} ${p.day}` : '';
  }

  // How long until the writer's next local midnight, in milliseconds.
  function msUntilMidnight(now) {
    const d = now instanceof Date ? now : new Date();
    const next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, 0, 0, 1);
    return Math.max(1000, next.getTime() - d.getTime());
  }

  // Call back whenever the local date changes: at midnight, and when the tab
  // comes back after the machine slept through one. Returns a stop function.
  function watchDay(callback) {
    if (typeof window === 'undefined') return () => {};
    let current = today();
    let timer = 0;
    function check() {
      const now = today();
      if (now !== current) {
        current = now;
        callback(now);
      }
    }
    function arm() {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { check(); arm(); }, msUntilMidnight(new Date()));
    }
    function onVisible() {
      if (document.visibilityState === 'visible') { check(); arm(); }
    }
    document.addEventListener('visibilitychange', onVisible);
    arm();
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }

  return {
    today,
    localIso,
    calendarDay,
    dayNumber,
    addDays,
    daysBetween,
    weekday,
    weekdayNumber,
    weekAnchorOf,
    mondayOf,
    formatDay,
    formatShort,
    msUntilMidnight,
    watchDay,
  };
});
