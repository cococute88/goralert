export interface MonthGridCell {
  date: Date;
  isoDate: string;
  day: number;
  month: number;
  year: number;
  isCurrentMonth: boolean;
}

function toIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Build a fixed 6-week / 7-column calendar grid for the month that contains `month`.
 * It intentionally uses only JavaScript Date and includes leading/trailing dates from
 * adjacent months so the UI never falls back to a list layout on small screens.
 */
export function buildMonthGrid(month: Date): MonthGridCell[] {
  const firstOfMonth = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = new Date(firstOfMonth);
  start.setDate(firstOfMonth.getDate() - firstOfMonth.getDay());

  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return {
      date,
      isoDate: toIsoDate(date),
      day: date.getDate(),
      month: date.getMonth() + 1,
      year: date.getFullYear(),
      isCurrentMonth: date.getMonth() === month.getMonth(),
    };
  });
}

export function formatIsoDate(date: Date): string {
  return toIsoDate(date);
}
