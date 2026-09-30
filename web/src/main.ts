import './style.css';
import { createWorker, OEM, PSM, type Worker, type Line } from 'tesseract.js';
import { setupSamplePanel, type Box, type SampleSource } from './samples';

// ---- 設定 ----
// 学習済みモデルの置き場所（web/public/models/starrail.traineddata）
const MODEL_NAME = 'starrail';
// 公開先の住所（BASE_URL）に合わせて、モデルの場所を決める
const MODEL_DIR = new URL(`${import.meta.env.BASE_URL}models`, location.href).href;
// 開発中（npm run dev）か、アドレスの最後に ?dev を付けたときだけ、開発用の機能を表示する
const DEV_MODE = import.meta.env.DEV || new URLSearchParams(location.search).has('dev');
// 画像全体を読むとき、結果に含める最低限の文字数（1文字だけの誤検出を減らす）
const MIN_LETTERS_FULL = 2;
// 範囲を選んで読むとき、文字の高さがこのくらい（ピクセル）になるまで拡大する
const TARGET_REGION_HEIGHT = 90;
// これより小さいドラッグは、範囲選択ではなくクリックとみなす（元画像のピクセル数）
const MIN_REGION_SIZE = 8;

// ---- 画面の部品を取得 ----
function $<T extends HTMLElement>(selector: string): T {
  const el = document.querySelector<T>(selector);
  if (!el) throw new Error(`${selector} が見つかりません`);
  return el;
}

const fileInput = $<HTMLInputElement>('#file-input');
const dropzone = $<HTMLLabelElement>('#dropzone');
const statusEl = $<HTMLParagraphElement>('#status');
const imagesSection = $<HTMLElement>('#images');
const originalWrap = $<HTMLDivElement>('#original-wrap');
const originalCanvas = $<HTMLCanvasElement>('#original-canvas');
const translatedCanvas = $<HTMLCanvasElement>('#translated-canvas');
const clearRegionsButton = $<HTMLButtonElement>('#clear-regions');
const sourceText = $<HTMLParagraphElement>('#source-text');
const translatedText = $<HTMLParagraphElement>('#translated-text');
const testInput = $<HTMLTextAreaElement>('#test-input');
const psmSelect = $<HTMLSelectElement>('#psm-select');
const scaleSelect = $<HTMLSelectElement>('#scale-select');
const confidenceInput = $<HTMLInputElement>('#confidence-input');
const confidenceValue = $<HTMLSpanElement>('#confidence-value');
const debugLines = $<HTMLUListElement>('#debug-lines');
const devTools = $<HTMLDetailsElement>('#dev-tools');

// ---- データの形 ----
type RecognizedLine = { text: string; confidence: number; box: Box; accepted: boolean };
type Region = {
  id: number;
  box: Box;
  area: Box; // 実際に切り抜いて読んだ範囲（選んだ範囲より少し広い）
  singleLine: boolean;
  lines: RecognizedLine[];
  state: 'reading' | 'done' | 'empty';
  el: HTMLDivElement;
};

// ---- 今の状態 ----
let imageLoaded = false;
let currentJob = 0; // 別の画像が読み込まれたとき、古い結果を捨てるための番号
let fullImageLines: RecognizedLine[] = []; // 画像全体を読んだ結果
let regions: Region[] = []; // ドラッグで選んだ範囲と、その結果
let nextRegionId = 1;
const samplePanel = setupSamplePanel(originalCanvas, DEV_MODE);
devTools.hidden = !DEV_MODE;

// ---- 表示まわり ----
function showStatus(message: string, kind: 'info' | 'error' = 'info') {
  statusEl.textContent = message;
  statusEl.dataset.kind = kind;
  statusEl.hidden = false;
}

function hideStatus() {
  statusEl.hidden = true;
}

// 内部の文字列は1つだけ。表示するフォントだけを変える。
function setText(text: string) {
  sourceText.textContent = text;
  translatedText.textContent = text;
}

// ---- 文字認識（Tesseract） ----
let workerPromise: Promise<Worker> | null = null;

function getWorker(): Promise<Worker> {
  if (!workerPromise) {
    workerPromise = createWorker(MODEL_NAME, OEM.LSTM_ONLY, {
      langPath: MODEL_DIR,
      gzip: false,
      cacheMethod: 'none', // モデルを差し替えたとき、古いモデルが使われないようにする
    }).catch((error) => {
      workerPromise = null;
      throw error;
    });
  }
  return workerPromise;
}

// 読み取りを1つずつ順番に実行する（設定の切り替えが混ざらないように）
let queue: Promise<unknown> = Promise.resolve();
function runInOrder<T>(task: () => Promise<T>): Promise<T> {
  const result = queue.then(task);
  queue = result.catch(() => undefined);
  return result;
}

function countLetters(text: string): number {
  return (text.match(/[A-Za-z]/g) ?? []).length;
}

// 画像の一部（area）を拡大して読み、元画像での位置つきで結果を返す
async function readArea(area: Box, scale: number, psm: PSM): Promise<Omit<RecognizedLine, 'accepted'>[]> {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(area.width * scale));
  canvas.height = Math.max(1, Math.round(area.height * scale));
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(originalCanvas, area.x, area.y, area.width, area.height, 0, 0, canvas.width, canvas.height);
  }

  return runInOrder(async () => {
    const worker = await getWorker();
    await worker.setParameters({ tessedit_pageseg_mode: psm });
    const { data } = await worker.recognize(canvas, {}, { blocks: true });
    const lines: Line[] = (data.blocks ?? []).flatMap((block) =>
      block.paragraphs.flatMap((paragraph) => paragraph.lines),
    );
    return lines
      .map((line) => ({
        text: line.text.trim(),
        confidence: line.confidence,
        box: {
          x: area.x + line.bbox.x0 / scale,
          y: area.y + line.bbox.y0 / scale,
          width: (line.bbox.x1 - line.bbox.x0) / scale,
          height: (line.bbox.y1 - line.bbox.y0) / scale,
        },
      }))
      .filter((line) => line.text.length > 0);
  });
}

async function readFullImage(): Promise<RecognizedLine[]> {
  const threshold = Number(confidenceInput.value);
  const whole = { x: 0, y: 0, width: originalCanvas.width, height: originalCanvas.height };
  const lines = await readArea(whole, Number(scaleSelect.value), psmSelect.value as PSM);
  return lines.map((line) => ({
    ...line,
    accepted: line.confidence >= threshold && countLetters(line.text) >= MIN_LETTERS_FULL,
  }));
}

// 選んだ範囲を、どう切り抜いてどう読むかを決める
function planRegion(box: Box): { area: Box; singleLine: boolean } {
  // 少しだけ周りも含めて切り抜く（文字の端が欠けないように）
  const margin = Math.max(3, box.height * 0.1);
  const area = clampBox({
    x: box.x - margin,
    y: box.y - margin,
    width: box.width + margin * 2,
    height: box.height + margin * 2,
  });
  // 横長の範囲は1行、縦に高い範囲は複数行として読む
  return { area, singleLine: box.height / box.width < 0.3 };
}

async function readRegion(region: Region): Promise<RecognizedLine[]> {
  const psm = region.singleLine ? PSM.SINGLE_LINE : PSM.SINGLE_BLOCK;
  const scale = Math.min(4, Math.max(Number(scaleSelect.value), TARGET_REGION_HEIGHT / region.box.height));
  const lines = await readArea(region.area, scale, psm);
  // 自分で範囲を選んだときは、信頼度が低くても結果に含める
  return lines.map((line) => ({ ...line, accepted: countLetters(line.text) >= 1 }));
}

// 学習データとして保存できる候補を作る
function sampleSources(): SampleSource[] {
  return regions.flatMap((region) => {
    const accepted = region.lines.filter((line) => line.accepted);
    if (region.state !== 'done' || accepted.length === 0) return [];
    if (region.singleLine) {
      // 1行として読んだ範囲は、切り抜いた範囲をそのまま使う
      return [{ key: `${region.id}`, box: region.area, text: accepted.map((l) => l.text).join(' ') }];
    }
    // 複数行の範囲は、1行ずつに分けて保存する（学習ツールは1行ずつの画像を使うため）
    return accepted.map((line, index) => {
      const padY = line.box.height * 0.25;
      const padX = line.box.height * 0.5;
      return {
        key: `${region.id}-${index}`,
        box: clampBox({
          x: line.box.x - padX,
          y: line.box.y - padY,
          width: line.box.width + padX * 2,
          height: line.box.height + padY * 2,
        }),
        text: line.text,
      };
    });
  });
}

// ---- 翻訳後画像を描く ----
// 文字の周りの色を調べて、背景の色として使う
function averageColorAround(ctx: CanvasRenderingContext2D, box: Box): [number, number, number] {
  const pad = 3;
  const x = Math.max(0, Math.floor(box.x - pad));
  const y = Math.max(0, Math.floor(box.y - pad));
  const w = Math.min(ctx.canvas.width - x, Math.ceil(box.width + pad * 2));
  const h = Math.min(ctx.canvas.height - y, Math.ceil(box.height + pad * 2));
  if (w <= 0 || h <= 0) return [0, 0, 0];

  const { data } = ctx.getImageData(x, y, w, h);
  let r = 0, g = 0, b = 0, count = 0;
  for (let py = 0; py < h; py++) {
    for (let px = 0; px < w; px++) {
      // 外周の2ピクセル分だけを使う（文字そのものの色を混ぜない）
      if (px > 1 && px < w - 2 && py > 1 && py < h - 2) continue;
      const i = (py * w + px) * 4;
      r += data[i];
      g += data[i + 1];
      b += data[i + 2];
      count++;
    }
  }
  return count ? [r / count, g / count, b / count] : [0, 0, 0];
}

function drawTranslation(lines: RecognizedLine[]) {
  const ctx = translatedCanvas.getContext('2d');
  if (!ctx) return;
  ctx.drawImage(originalCanvas, 0, 0);

  // 先にすべての背景色を調べてから描く（塗りつぶした色を次の行が拾わないように）
  const colors = lines.map((line) => averageColorAround(ctx, line.box));

  lines.forEach((line, index) => {
    const [r, g, b] = colors[index];
    const { x, y, width, height } = line.box;
    const pad = 2;
    ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
    ctx.fillRect(x - pad, y - pad, width + pad * 2, height + pad * 2);

    // 背景が暗ければ白、明るければ黒で書く
    const brightness = 0.299 * r + 0.587 * g + 0.114 * b;
    ctx.fillStyle = brightness < 140 ? '#ffffff' : '#111111';

    // 枠の高さに合わせた文字の大きさにし、はみ出す場合は縮める
    let fontSize = height * 0.8;
    const fontFamily = '"Zen Kaku Gothic New", sans-serif';
    ctx.font = `700 ${fontSize}px ${fontFamily}`;
    const measured = ctx.measureText(line.text).width;
    if (measured > width) {
      fontSize *= width / measured;
      ctx.font = `700 ${fontSize}px ${fontFamily}`;
    }
    ctx.textBaseline = 'middle';
    ctx.fillText(line.text, x, y + height / 2);
  });
}

// ---- 結果をまとめて表示する ----
function currentLines(): RecognizedLine[] {
  // 範囲を選んでいるときはその結果だけ、選んでいないときは画像全体の結果を使う
  const all = regions.length > 0 ? regions.flatMap((region) => region.lines) : fullImageLines;
  // 上から下、左から右の順に並べる
  return [...all].sort((a, b) => a.box.y - b.box.y || a.box.x - b.box.x);
}

function render() {
  const lines = currentLines();
  const accepted = lines.filter((line) => line.accepted);

  debugLines.replaceChildren(
    ...lines.map((line) => {
      const li = document.createElement('li');
      li.textContent = `${line.text}（信頼度 ${Math.round(line.confidence)}）`;
      li.classList.toggle('is-rejected', !line.accepted);
      return li;
    }),
  );

  drawTranslation(accepted);
  setText(accepted.map((line) => line.text).join('\n'));
  clearRegionsButton.hidden = regions.length === 0;
  samplePanel.update(sampleSources());

  const reading = regions.some((region) => region.state === 'reading');
  if (reading) {
    showStatus('選んだ範囲を読んでいます…');
  } else if (regions.length > 0 && regions.at(-1)?.state === 'empty') {
    showStatus('選んだ範囲から文字を読み取れませんでした。文字にぴったり合わせて囲み直してみてください。', 'error');
  } else if (accepted.length === 0) {
    showStatus(
      '画像からスタレ文字を認識できませんでした。読みたい文字をドラッグで囲むと、その部分だけを読み取れます。（羅浮の文字には対応していません）',
      'error',
    );
  } else {
    hideStatus();
  }
}

// ---- 範囲選択 ----
function clampBox(box: Box): Box {
  const x = Math.max(0, box.x);
  const y = Math.max(0, box.y);
  return {
    x,
    y,
    width: Math.min(originalCanvas.width, box.x + box.width) - x,
    height: Math.min(originalCanvas.height, box.y + box.height) - y,
  };
}

// 画面上の位置を、元画像のピクセル位置に変換する
function toImagePoint(e: PointerEvent): { x: number; y: number } {
  const rect = originalCanvas.getBoundingClientRect();
  return {
    x: ((e.clientX - rect.left) / rect.width) * originalCanvas.width,
    y: ((e.clientY - rect.top) / rect.height) * originalCanvas.height,
  };
}

function makeSelectionElement(): HTMLDivElement {
  const el = document.createElement('div');
  el.className = 'selection';
  originalWrap.append(el);
  return el;
}

// 選択枠は％で配置して、画面の大きさが変わってもずれないようにする
function placeSelection(el: HTMLDivElement, box: Box) {
  el.style.left = `${(box.x / originalCanvas.width) * 100}%`;
  el.style.top = `${(box.y / originalCanvas.height) * 100}%`;
  el.style.width = `${(box.width / originalCanvas.width) * 100}%`;
  el.style.height = `${(box.height / originalCanvas.height) * 100}%`;
}

function boxFromPoints(a: { x: number; y: number }, b: { x: number; y: number }): Box {
  return clampBox({
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(a.x - b.x),
    height: Math.abs(a.y - b.y),
  });
}

let dragStart: { x: number; y: number } | null = null;
let draftEl: HTMLDivElement | null = null;

originalWrap.addEventListener('pointerdown', (e) => {
  if (!imageLoaded || e.button !== 0) return;
  originalWrap.setPointerCapture(e.pointerId);
  dragStart = toImagePoint(e);
  draftEl = makeSelectionElement();
  draftEl.classList.add('is-drawing');
  placeSelection(draftEl, boxFromPoints(dragStart, dragStart));
});

originalWrap.addEventListener('pointermove', (e) => {
  if (!dragStart || !draftEl) return;
  placeSelection(draftEl, boxFromPoints(dragStart, toImagePoint(e)));
});

function finishDrag(e: PointerEvent) {
  if (!dragStart || !draftEl) return;
  const box = boxFromPoints(dragStart, toImagePoint(e));
  const el = draftEl;
  dragStart = null;
  draftEl = null;

  if (box.width < MIN_REGION_SIZE || box.height < MIN_REGION_SIZE) {
    el.remove(); // クリックしただけなので何もしない
    return;
  }
  el.classList.remove('is-drawing');
  addRegion(box, el);
}

originalWrap.addEventListener('pointerup', finishDrag);
originalWrap.addEventListener('pointercancel', () => {
  draftEl?.remove();
  dragStart = null;
  draftEl = null;
});

async function addRegion(box: Box, el: HTMLDivElement) {
  const job = currentJob;
  const region: Region = { id: nextRegionId++, box, ...planRegion(box), lines: [], state: 'reading', el };
  el.classList.add('is-reading');
  regions.push(region);
  render();

  try {
    region.lines = await readRegion(region);
  } catch (error) {
    console.error(error);
  }
  if (job !== currentJob || !regions.includes(region)) return; // 途中で画像や選択が変わった

  region.state = region.lines.some((line) => line.accepted) ? 'done' : 'empty';
  el.classList.remove('is-reading');
  el.classList.toggle('is-empty', region.state === 'empty');
  render();
}

clearRegionsButton.addEventListener('click', () => {
  regions.forEach((region) => region.el.remove());
  regions = [];
  render();
});

// ---- 画像の読み込み ----
async function loadImage(file: File): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function drawToCanvas(canvas: HTMLCanvasElement, img: HTMLImageElement) {
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext('2d')?.drawImage(img, 0, 0);
}

async function handleFile(file: File) {
  if (!file.type.startsWith('image/')) {
    showStatus('画像ファイルを選んでください（PNG・JPEGなど）。', 'error');
    return;
  }

  let img: HTMLImageElement;
  try {
    img = await loadImage(file);
  } catch {
    showStatus('画像を読み込めませんでした。別のファイルで試してください。', 'error');
    return;
  }

  const job = ++currentJob;
  regions.forEach((region) => region.el.remove());
  regions = [];
  fullImageLines = [];
  samplePanel.reset();
  drawToCanvas(originalCanvas, img);
  drawToCanvas(translatedCanvas, img);
  imageLoaded = true;
  imagesSection.hidden = false;
  setText('');
  debugLines.replaceChildren();
  clearRegionsButton.hidden = true;
  showStatus(workerPromise ? '画像全体から文字を探しています…' : '文字認識モデルを読み込んでいます…');

  let lines: RecognizedLine[];
  try {
    lines = await readFullImage();
  } catch (error) {
    console.error(error);
    if (job === currentJob) {
      showStatus(
        '文字認識モデルを読み込めませんでした。web/public/models/starrail.traineddata があるか確認してください。',
        'error',
      );
    }
    return;
  }
  if (job !== currentJob) return; // 途中で別の画像が選ばれた

  fullImageLines = lines;
  render();
}

// ---- 操作の受け付け ----
fileInput.addEventListener('change', () => {
  const file = fileInput.files?.[0];
  if (file) handleFile(file);
  fileInput.value = ''; // 同じファイルをもう一度選べるようにする
});

dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('is-dragging');
});

dropzone.addEventListener('dragleave', () => {
  dropzone.classList.remove('is-dragging');
});

dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('is-dragging');
  const file = e.dataTransfer?.files[0];
  if (file) handleFile(file);
});

window.addEventListener('paste', (e) => {
  const file = [...(e.clipboardData?.files ?? [])].find((f) => f.type.startsWith('image/'));
  if (file) handleFile(file);
});

testInput.addEventListener('input', () => setText(testInput.value));
if (DEV_MODE) setText(testInput.value);

confidenceInput.addEventListener('input', () => {
  confidenceValue.textContent = confidenceInput.value;
});

// ---- フォントが読み込めたか確認 ----
document.fonts
  .load('1em StarRailFont')
  .then((faces) => {
    if (faces.length === 0) throw new Error();
  })
  .catch(() => {
    showStatus(
      'スタレ文字フォントを読み込めませんでした。web/public/fonts/starrail.ttf があるか確認してください。',
      'error',
    );
  });
