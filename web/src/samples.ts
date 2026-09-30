// ---- 学習データの保存 ----
// 範囲を選んで読んだ行を、正しい英文に直して「画像（.png）＋正解（.gt.txt）」として保存する

export type Box = { x: number; y: number; width: number; height: number };
export type SampleSource = { key: string; box: Box; text: string };

// Chrome・Edgeの「フォルダに直接保存する」機能（TypeScriptの標準の型にまだ無いので宣言する）
declare global {
  interface Window {
    showDirectoryPicker?: (options?: {
      id?: string;
      mode?: 'read' | 'readwrite';
    }) => Promise<FileSystemDirectoryHandle>;
  }
}

type SampleState = { text: string; saved: boolean };

function $<T extends HTMLElement>(selector: string): T {
  const el = document.querySelector<T>(selector);
  if (!el) throw new Error(`${selector} が見つかりません`);
  return el;
}

export function setupSamplePanel(sourceCanvas: HTMLCanvasElement, enabled: boolean) {
  const panel = $<HTMLDetailsElement>('#sample-panel');
  const pickFolderButton = $<HTMLButtonElement>('#pick-folder');
  const folderName = $<HTMLSpanElement>('#folder-name');
  const areaSelect = $<HTMLSelectElement>('#area-select');
  const list = $<HTMLUListElement>('#sample-list');
  const saveAllButton = $<HTMLButtonElement>('#save-all');
  const statusEl = $<HTMLParagraphElement>('#sample-status');

  let folder: FileSystemDirectoryHandle | null = null;
  let sources: SampleSource[] = [];
  const states = new Map<string, SampleState>(); // 入力中の文字や保存済みかどうかを覚えておく
  let counter = 0;

  if (!window.showDirectoryPicker) {
    pickFolderButton.hidden = true;
    folderName.textContent = 'このブラウザではフォルダを選べないため、ダウンロードフォルダに保存されます';
  }

  pickFolderButton.addEventListener('click', async () => {
    try {
      folder = (await window.showDirectoryPicker?.({ id: 'starrail-samples', mode: 'readwrite' })) ?? null;
      if (folder) folderName.textContent = `保存先：${folder.name}`;
    } catch {
      // キャンセルされたときは何もしない
    }
  });

  // 元画像から、指定した範囲だけを元の解像度のまま切り抜く
  function crop(box: Box): HTMLCanvasElement {
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(box.width));
    canvas.height = Math.max(1, Math.round(box.height));
    canvas
      .getContext('2d')
      ?.drawImage(sourceCanvas, box.x, box.y, box.width, box.height, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  function makeFileName(): string {
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
    counter += 1;
    return `${areaSelect.value}_${stamp}_${counter}`;
  }

  async function writeFile(name: string, data: Blob) {
    if (folder) {
      const handle = await folder.getFileHandle(name, { create: true });
      const writable = await handle.createWritable();
      await writable.write(data);
      await writable.close();
      return;
    }
    // フォルダを選んでいないときは、ダウンロードとして保存する
    const url = URL.createObjectURL(data);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function save(source: SampleSource): Promise<boolean> {
    const state = states.get(source.key);
    const text = (state?.text ?? source.text).trim();
    if (!state || state.saved || text === '') return false;

    const image = await new Promise<Blob | null>((resolve) => crop(source.box).toBlob(resolve, 'image/png'));
    if (!image) throw new Error('画像を作れませんでした');

    const name = makeFileName();
    await writeFile(`${name}.png`, image);
    // 学習ツールが読めるよう、改行はLinux式（\n）にする
    await writeFile(`${name}.gt.txt`, new Blob([`${text}\n`], { type: 'text/plain' }));
    state.saved = true;
    statusEl.textContent = `保存しました：${name}`;
    return true;
  }

  async function saveWithMessage(task: () => Promise<unknown>) {
    try {
      await task();
    } catch (error) {
      console.error(error);
      statusEl.textContent = '保存できませんでした。保存先フォルダを選び直してみてください。';
    }
    renderList();
  }

  saveAllButton.addEventListener('click', () =>
    saveWithMessage(async () => {
      let count = 0;
      for (const source of sources) {
        if (await save(source)) count += 1;
      }
      statusEl.textContent = count > 0 ? `${count}件を保存しました` : '保存するものはありません';
    }),
  );

  function renderList() {
    list.replaceChildren(
      ...sources.map((source) => {
        const state = states.get(source.key)!;
        const li = document.createElement('li');
        li.classList.toggle('is-saved', state.saved);

        const preview = crop(source.box);
        preview.className = 'sample-preview';

        const input = document.createElement('input');
        input.type = 'text';
        input.value = state.text;
        input.disabled = state.saved;
        input.setAttribute('aria-label', '正しい英文');
        input.spellcheck = false;
        input.addEventListener('input', () => {
          state.text = input.value;
        });

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'text-button';
        button.textContent = state.saved ? '保存済み' : '保存';
        button.disabled = state.saved;
        button.addEventListener('click', () => saveWithMessage(() => save(source)));

        li.append(preview, input, button);
        return li;
      }),
    );
    saveAllButton.disabled = !sources.some((source) => !states.get(source.key)?.saved);
  }

  return {
    // 範囲選択の結果が変わるたびに呼ぶ
    update(next: SampleSource[]) {
      if (!enabled) return; // 一般公開のページでは表示しない
      sources = next;
      for (const source of next) {
        if (!states.has(source.key)) states.set(source.key, { text: source.text, saved: false });
      }
      panel.hidden = next.length === 0;
      renderList();
    },
    // 新しい画像を読み込んだときに呼ぶ
    reset() {
      sources = [];
      states.clear();
      statusEl.textContent = '';
      panel.hidden = true;
      list.replaceChildren();
    },
  };
}
