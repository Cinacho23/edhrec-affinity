export function decodeCardFeatures(encoded, scale, width, count) {
  if (!encoded || !width) return [];
  const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
  if (bytes.length !== 2 * width * count) throw new Error("The card features are incomplete.");
  const values = new DataView(bytes.buffer);
  const features = new Float32Array(width * count);
  for (let index = 0; index < features.length; index++) features[index] = values.getInt16(index * 2, true) / scale;
  return Array.from({ length: count }, (_, index) => features.subarray(index * width, (index + 1) * width));
}

function average(cards, width) {
  const means = new Float32Array(width);
  let count = 0;
  for (const { features } of cards) {
    if (!features) continue;
    for (let index = 0; index < width; index++) means[index] += features[index];
    count++;
  }
  if (count) for (let index = 0; index < width; index++) means[index] /= count;
  return means;
}

// Algebraically combine the first layer's fixed commander/context inputs and
// their crosses with the candidate. Inference then visits each feature once.
export function neuralScores(cards, commanders, context, model) {
  if (!model?.feature_count) return new Map();
  const width = model.feature_count;
  const command = average(commanders, width);
  const partial = average(context, width);
  const layers = model.weights.map((row) => Float32Array.from(row));
  const outputs = model.biases.map((row) => new Float32Array(row.length));
  const hidden = outputs[0].length;
  const first = layers[0];
  const combined = new Float32Array(width * hidden);
  const bias = Float32Array.from(model.biases[0]);
  for (let i = 0; i < width; i++) {
    for (let j = 0; j < hidden; j++) {
      const at = i * hidden + j;
      const stride = width * hidden;
      bias[j] += command[i] * first[at + stride] + partial[i] * first[at + 2 * stride];
      combined[at] = first[at] + command[i] * first[at + 3 * stride] + partial[i] * first[at + 4 * stride];
    }
  }
  const raw = [];
  for (const card of cards) {
    const vector = card.features;
    if (!vector) continue;
    const firstOutput = outputs[0];
    firstOutput.set(bias);
    for (let i = 0; i < width; i++) {
      const value = vector[i];
      if (!value) continue;
      const offset = i * hidden;
      for (let j = 0; j < hidden; j++) firstOutput[j] += value * combined[offset + j];
    }
    for (let layer = 1; layer < layers.length; layer++) {
      const input = outputs[layer - 1], output = outputs[layer];
      output.set(model.biases[layer]);
      for (let i = 0; i < input.length; i++) {
        const value = Math.max(0, input[i]);
        if (!value) continue;
        const offset = i * output.length;
        for (let j = 0; j < output.length; j++) output[j] += value * layers[layer][offset + j];
      }
    }
    raw.push([card.name, outputs.at(-1)[0]]);
  }
  const ordered = raw.map(([, value]) => value).sort((left, right) => left - right);
  const center = ordered[Math.floor(ordered.length / 2)] || 0;
  const scale = Math.max(.001, (ordered[Math.floor(.95 * (ordered.length - 1))] || 0) - center);
  return new Map(raw.map(([name, value]) => [name, Math.max(-2, Math.min(4, (value - center) / scale))]));
}
