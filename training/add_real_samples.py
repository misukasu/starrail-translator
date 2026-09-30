"""本物のゲーム画像の切り抜き（finetune/）を、学習データに加えるプログラム

アプリで保存した「画像（.png）＋正解（.gt.txt）」のペアを読み込み、
1枚ごとに少しずつ変形させた画像をたくさん作って、学習用フォルダに追加します。
本物の画像は枚数が少ないので、変形させて数を増やすことで、合成データに埋もれないようにします。

使い方（generate_data.py で合成データを作った「後」に実行します）:
    python add_real_samples.py --output ~/tesstrain/data/starrail-ground-truth
"""

import argparse
import io
import random
from collections import Counter
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageStat

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE.parent / "finetune"
DEFAULT_OUTPUT = HERE / "output" / "ground-truth"
PREFIX = "real_"  # 合成データと区別するため、ファイル名の先頭に付ける


def load_samples(folder: Path, exclude: set[str]) -> list[tuple[Path, str]]:
    samples = []
    for image_path in sorted(folder.glob("*.png")):
        gt_path = image_path.with_name(image_path.stem + ".gt.txt")
        if not gt_path.exists():
            print(f"正解テキストが無いのでスキップ: {image_path.name}")
            continue
        # Windowsで編集した場合の改行（\r）も取り除き、1行にまとめる
        text = " ".join(gt_path.read_text(encoding="utf-8").replace("\r", "").split())
        if not text:
            print(f"正解テキストが空なのでスキップ: {gt_path.name}")
            continue
        area = image_path.stem.split("_")[0]
        if area in exclude:
            continue
        samples.append((image_path, text))
    return samples


def augment(img: Image.Image) -> Image.Image:
    """本物の画像を、見た目が少しずつ違うように変形する"""
    w, h = img.size

    # 余白を少し削って、切り抜き方のばらつきを再現する（文字は削らない程度に）
    left = int(w * random.uniform(0, 0.03))
    right = w - int(w * random.uniform(0, 0.03))
    top = int(h * random.uniform(0, 0.05))
    bottom = h - int(h * random.uniform(0, 0.05))
    img = img.crop((left, top, right, bottom))

    # 大きさを変える（アプリでは拡大してから読むので、大きめも混ぜる）
    target_height = random.randint(32, 96)
    scale = target_height / img.height
    width_scale = scale * random.uniform(0.9, 1.1)
    img = img.resize((max(1, int(img.width * width_scale)), target_height), Image.BICUBIC)

    # わずかに傾ける（はみ出た部分は周りの平均的な色で埋める）
    fill = tuple(int(v) for v in ImageStat.Stat(img).mean[:3])
    img = img.rotate(random.uniform(-1.5, 1.5), resample=Image.BICUBIC, expand=True, fillcolor=fill)

    # 明るさとコントラストを変える
    img = ImageEnhance.Brightness(img).enhance(random.uniform(0.8, 1.2))
    img = ImageEnhance.Contrast(img).enhance(random.uniform(0.75, 1.25))

    if random.random() < 0.3:
        img = img.filter(ImageFilter.GaussianBlur(random.uniform(0.3, 1.0)))
    if random.random() < 0.3:
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=random.randint(40, 90))
        img = Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")
    return img


def main():
    parser = argparse.ArgumentParser(description="本物の画像の切り抜きを学習データに加えます")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="アプリで保存したデータのフォルダ")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="学習用フォルダ")
    parser.add_argument("--copies", type=int, default=30, help="1枚あたり何枚に増やすか")
    parser.add_argument(
        "--exclude", nargs="*", default=["amphoreus", "unknown"],
        help="使わない地域（ファイル名の先頭の名前）",
    )
    parser.add_argument("--seed", type=int, default=None, help="同じ結果を再現したいときの乱数の種")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"フォルダが見つかりません: {args.input}")
    if args.seed is not None:
        random.seed(args.seed)

    samples = load_samples(args.input, set(args.exclude))
    if not samples:
        raise SystemExit("使えるデータがありませんでした")

    args.output.mkdir(parents=True, exist_ok=True)
    # 前回追加した本物のデータ（と、そこから作られた学習ツールのファイル）を消してから作り直す
    for old in args.output.glob(f"{PREFIX}*"):
        old.unlink()

    counts = Counter()
    for image_path, text in samples:
        original = Image.open(image_path).convert("RGB")
        area = image_path.stem.split("_")[0]
        counts[area] += 1
        for k in range(args.copies):
            img = augment(original)
            name = f"{PREFIX}{image_path.stem}_{k:03d}"
            img.save(args.output / f"{name}.png")
            (args.output / f"{name}.gt.txt").write_text(text + "\n", encoding="utf-8", newline="\n")

    print("地域ごとの枚数（元の画像）:")
    for area, count in counts.most_common():
        print(f"  {area}: {count}枚")
    total = sum(counts.values())
    print(f"完了: {total}枚 × {args.copies} = {total * args.copies}組を {args.output} に追加しました")


if __name__ == "__main__":
    main()
