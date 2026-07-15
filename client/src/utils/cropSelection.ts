export type CropSelectionInput = {
  artifact: string;
};

export function updateCropSelection(
  visible: CropSelectionInput[],
  current: Set<string>,
  clickedArtifact: string,
  anchorArtifact: string | null,
  modifiers: { shift: boolean; additive: boolean },
): { selected: Set<string>; anchor: string } {
  if (modifiers.shift && anchorArtifact) {
    const anchorIndex = visible.findIndex((crop) => crop.artifact === anchorArtifact);
    const clickedIndex = visible.findIndex((crop) => crop.artifact === clickedArtifact);
    if (anchorIndex >= 0 && clickedIndex >= 0) {
      const start = Math.min(anchorIndex, clickedIndex);
      const end = Math.max(anchorIndex, clickedIndex);
      const selected = modifiers.additive ? new Set(current) : new Set<string>();
      visible.slice(start, end + 1).forEach((crop) => selected.add(crop.artifact));
      return { selected, anchor: anchorArtifact };
    }
  }
  if (modifiers.additive) {
    const selected = new Set(current);
    if (selected.has(clickedArtifact)) selected.delete(clickedArtifact);
    else selected.add(clickedArtifact);
    return { selected, anchor: clickedArtifact };
  }
  return { selected: new Set([clickedArtifact]), anchor: clickedArtifact };
}

