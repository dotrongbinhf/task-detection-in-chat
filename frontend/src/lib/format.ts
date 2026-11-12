// Định dạng ngày giờ theo Asia/Ho_Chi_Minh (+07:00) cho UI.
const OFF = 7 * 3600_000;

function parts(iso: string) {
  const d = new Date(Date.parse(iso) + OFF);
  const wd = d.getUTCDay();
  return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(), hh: d.getUTCHours(), mm: d.getUTCMinutes(), wd: wd === 0 ? 7 : wd };
}
const p2 = (n: number) => String(n).padStart(2, "0");
export const viWeekday = (n: number) => (n === 7 ? "CN" : `T${n + 1}`);
export const viWeekdayLong = (n: number) => (n === 7 ? "Chủ nhật" : `Thứ ${n + 1}`);

export function fmtTime(iso: string) {
  const x = parts(iso);
  return `${p2(x.hh)}:${p2(x.mm)}`;
}
export function fmtDay(iso: string) {
  const x = parts(iso);
  return `${viWeekdayLong(x.wd)}, ${p2(x.d)}/${p2(x.m)}/${x.y}`;
}
export function fmtDayTime(iso: string) {
  const x = parts(iso);
  return `${viWeekday(x.wd)} ${p2(x.d)}/${p2(x.m)} ${p2(x.hh)}:${p2(x.mm)}`;
}
export function dayKey(iso: string) {
  const x = parts(iso);
  return `${x.y}-${p2(x.m)}-${p2(x.d)}`;
}
export function fmtDueDate(date: string | null) {
  if (!date) return "—";
  const x = parts(`${date}T00:00:00+07:00`);
  return `${viWeekday(x.wd)} ${p2(x.d)}/${p2(x.m)}`;
}
export function displayDue(t: { due_type: string; due_date: string | null; due_text: string | null }) {
  if (t.due_type === "calendar") return fmtDueDate(t.due_date);
  if (t.due_type === "event") return t.due_text ?? "theo sự kiện";
  return "Không hạn";
}
export function pct(v: number | null | undefined, digits = 0) {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}
