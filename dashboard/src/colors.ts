// Paleta categórica validada por la skill `dataviz` (brief de T3.2): orden
// fijo, nunca cíclico. Pasa contraste y separación por daltonismo calculados,
// no a ojo (ver el brief para los números exactos).
export const CATEGORY_COLORS = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#6250d6",
  "#e34948",
];

export function categoryColor(index: number): string {
  return CATEGORY_COLORS[index % CATEGORY_COLORS.length];
}
