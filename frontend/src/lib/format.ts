/** "2 h 05" for >= 1 hour, "48 min" below. */
export function formatDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, '0')}`
  return `${minutes} min`
}

/** "41 km" for >= 10 km, "1.4 km" below. */
export function formatKm(meters: number): string {
  const km = meters / 1000
  if (km >= 10) return `${Math.round(km)} km`
  return `${(Math.round(km * 10) / 10).toString()} km`
}

/** Signed whole minutes: "+8", "−3", "+0" (typographic minus). */
export function formatSignedMinutes(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return minutes < 0 ? `−${-minutes}` : `+${minutes}`
}

/** Unsigned whole minutes. */
export function minutes(seconds: number): number {
  return Math.round(seconds / 60)
}
