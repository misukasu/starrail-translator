"""スタレ文字の合成学習データを作るプログラム

英文をスタレ文字フォントで画像に描き、「画像（.png）」と「正解テキスト（.gt.txt）」の
ペアを output/ground-truth/ に大量に作ります。

使い方（training フォルダで、仮想環境に入った状態で）:
    python generate_data.py              # 5000枚作る
    python generate_data.py --count 200  # 枚数を指定する
"""

import argparse
import io
import random
import string
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

HERE = Path(__file__).resolve().parent
DEFAULT_FONT = HERE.parent / "web" / "public" / "fonts" / "starrail.ttf"
DEFAULT_OUTPUT = HERE / "output" / "ground-truth"
BACKGROUND_DIR = HERE / "backgrounds"
CORPUS_FILE = HERE / "corpus.txt"

# 数字や記号はスタレ文字ではないので、普通のフォントで描く
LATIN_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/mnt/c/Windows/Fonts/arial.ttf",  # WSL（Ubuntu）から実行したとき
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

ALLOWED_CHARS = set(string.ascii_letters + string.digits + " .,!?:;-'\"%&()/#+")
MAX_LENGTH = 40

# corpus.txt が無いときや、足りないときに使う単語
WORDS = """
the a an of to and in is it you that for on with as are be this at by from
welcome aboard express train station ticket danger warning keep out closed open
exit entrance north south east west left right up down stop go wait please
caution restricted area staff only no entry notice today news shop store market
hotel cafe bar museum library school hospital police office bank post park
astral trailblazer stellaron aeon herta space station belobog jarilo xianzhou
luofu penacony dream reverie march conductor navigator passenger journey
quiet zone lost found information guide map floor level room hall gate platform
first second last next previous new old big small hot cold good bad best
light dark star moon sun sky sea city world road street bridge tower wall
""".split()

# 線を太くする変形の強さ（確率, 最小の太さ, 最大の太さ）。太さは文字の大きさに対する割合
# ・light：フォントの線の太さがゲームとほぼ同じとき（自作フォント）
# ・heavy：フォントの線がゲームよりかなり細いとき（以前のファン製フォント）
BOLD_PROFILES = {
    "none": (0.0, 0.0, 0.0),
    "light": (0.3, 1 / 40, 1 / 20),
    "heavy": (0.7, 1 / 20, 1 / 8),
}
bold_profile = BOLD_PROFILES["light"]

SYMBOLS = [".", ",", "!", "?", ":", "-", "'", "%", "&", "/", "#"]


# ---------------------------------------------------------------- テキスト作り
def load_corpus(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = "".join(ch for ch in line.strip() if ch in ALLOWED_CHARS)
        if line.strip():
            lines.append(" ".join(line.split()))
    return lines


def change_case(text: str) -> str:
    r = random.random()
    if r < 0.4:
        return text.upper()
    if r < 0.6:
        return text.lower()
    if r < 0.8:
        return text.title()
    return text


def shorten(text: str) -> str:
    """長すぎる行を、単語の切れ目でランダムな位置から切り出す"""
    words = text.split()
    if len(text) <= MAX_LENGTH:
        return text
    start = random.randrange(len(words))
    result = []
    for word in words[start:]:
        if len(" ".join(result + [word])) > MAX_LENGTH:
            break
        result.append(word)
    return " ".join(result) or words[start][:MAX_LENGTH]


def word_line() -> str:
    words = random.choices(WORDS, k=random.randint(1, 6))
    if random.random() < 0.3:
        words.insert(random.randrange(len(words) + 1), str(random.randint(0, 9999)))
    line = " ".join(words)
    if random.random() < 0.4:
        line += random.choice(SYMBOLS)
    return line


def random_letters() -> str:
    """珍しい文字（q, x, z など）も学習できるよう、でたらめな文字列も混ぜる"""
    groups = [
        "".join(random.choices(string.ascii_letters, k=random.randint(2, 8)))
        for _ in range(random.randint(1, 5))
    ]
    return " ".join(groups)


def make_text(corpus: list[str]) -> str:
    r = random.random()
    if corpus and r < 0.5:
        text = change_case(shorten(random.choice(corpus)))
    elif r < 0.8:
        text = change_case(word_line())
    else:
        text = random_letters()
    return text[:MAX_LENGTH].strip() or "Astral Express"


# ---------------------------------------------------------------- 画像作り
class Fonts:
    """文字の大きさごとにフォントを読み込んで使い回す"""

    def __init__(self, starrail_path: Path):
        self.starrail_path = starrail_path
        self.latin_path = next((p for p in LATIN_FONT_CANDIDATES if Path(p).exists()), None)
        self.cache: dict[int, tuple] = {}

    def get(self, size: int):
        if size not in self.cache:
            starrail = ImageFont.truetype(str(self.starrail_path), size)
            if self.latin_path:
                latin = ImageFont.truetype(self.latin_path, size)
            else:
                latin = ImageFont.load_default(size)
            self.cache[size] = (starrail, latin)
        return self.cache[size]


def render_text(text: str, fonts: tuple, color: tuple, spacing: int, stroke: int = 0) -> Image.Image:
    """1文字ずつ、アルファベットはスタレ文字、それ以外は普通のフォントで描く"""
    starrail, latin = fonts
    pick = [starrail if ch in string.ascii_letters else latin for ch in text]
    advances = [font.getlength(ch) for font, ch in zip(pick, text)]
    ascent = max(f.getmetrics()[0] for f in (starrail, latin))
    descent = max(f.getmetrics()[1] for f in (starrail, latin))

    pad = spacing + stroke + 4
    width = int(sum(advances) + (spacing + stroke) * len(text)) + pad * 2
    height = ascent + descent + pad * 2
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    x = pad
    for font, ch, adv in zip(pick, text, advances):
        if ch != " ":
            draw.text((x, pad + ascent), ch, font=font, fill=color, anchor="ls",
                      stroke_width=stroke, stroke_fill=color)
        x += adv + spacing + stroke
    return layer


def make_background(size: tuple[int, int], backgrounds: list[Path]) -> Image.Image:
    w, h = size
    if backgrounds and random.random() < 0.8:
        src = Image.open(random.choice(backgrounds)).convert("RGB")
        scale = max(w / src.width, h / src.height, random.uniform(0.4, 1.2))
        src = src.resize((max(w, int(src.width * scale)), max(h, int(src.height * scale))))
        left = random.randint(0, src.width - w)
        top = random.randint(0, src.height - h)
        return src.crop((left, top, left + w, top + h))

    # 背景画像が無いときは、無地やグラデーションで代用する
    c1 = np.array([random.randint(0, 255) for _ in range(3)], dtype=float)
    c2 = c1 + np.random.uniform(-60, 60, 3) if random.random() < 0.5 else c1
    ramp = np.linspace(0, 1, w)[None, :, None]
    arr = c1 * (1 - ramp) + c2 * ramp
    arr = np.repeat(arr, h, axis=0)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def text_color_for(background: Image.Image) -> tuple:
    """背景の明るさに対して、読める程度に差のある文字色を選ぶ"""
    brightness = sum(ImageStat.Stat(background.convert("L")).mean)
    # ゲームの看板は、背景と文字の明るさの差が小さいことが多い
    difference = random.randint(60, 110) if random.random() < 0.4 else random.randint(120, 200)
    if brightness < 128:
        base = min(255, int(brightness) + difference)
    else:
        base = max(0, int(brightness) - difference)
    tint = [max(0, min(255, base + random.randint(-25, 25))) for _ in range(3)]
    return (*tint, 255)


def degrade(img: Image.Image) -> Image.Image:
    """ぼかし・解像度の低下・ノイズ・JPEG劣化で、スクリーンショットらしさを出す"""
    if random.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(random.uniform(0.3, 2.0)))
    if random.random() < 0.4:
        factor = random.uniform(0.4, 0.9)
        small = img.resize((max(1, int(img.width * factor)), max(1, int(img.height * factor))))
        img = small.resize(img.size)
    if random.random() < 0.3:
        arr = np.asarray(img).astype(float)
        arr += np.random.normal(0, random.uniform(3, 12), arr.shape)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if random.random() < 0.4:
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=random.randint(30, 90))
        img = Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")
    return img


def vary_shape(mask: Image.Image, size: int) -> Image.Image:
    """地域ごとのデザイン違いに強くなるよう、文字の形を少しずつ変える
    （フォントファイル自体は変更せず、描いた画像の方を変形する）"""
    w, h = mask.size
    # 横長・縦長にする
    sx = random.uniform(0.85, 1.25)
    sy = random.uniform(0.9, 1.1)
    mask = mask.resize((max(1, int(w * sx)), max(1, int(h * sy))), Image.BICUBIC)

    # 斜体のように傾ける
    if random.random() < 0.4:
        shear = random.uniform(-0.25, 0.25)
        w, h = mask.size
        extra = int(abs(shear) * h)
        offset = -extra if shear > 0 else 0
        mask = mask.transform((w + extra, h), Image.AFFINE, (1, shear, offset, 0, 1, 0),
                              resample=Image.BICUBIC)

    # 線を細くする（太くするのは render_text の stroke で行う）
    if size >= 36 and random.random() < 0.2:
        alpha = mask.getchannel("A").filter(ImageFilter.MinFilter(3))
        mask.putalpha(alpha)
    return mask


def colorize(mask: Image.Image, color: tuple) -> Image.Image:
    """白で描いた文字の形に、指定した色を付ける"""
    colored = Image.new("RGBA", mask.size, color)
    colored.putalpha(mask.getchannel("A"))
    return colored


def make_sample(text: str, fonts: Fonts, backgrounds: list[Path]) -> Image.Image:
    size = random.randint(28, 64)
    spacing = random.randint(0, size // 8)

    # まず白で文字の形だけを作り、少し傾ける
    # 線を太くする（強さは --bold で切り替える）
    probability, low, high = bold_profile
    if random.random() < probability:
        stroke = random.randint(max(1, int(size * low)), max(1, int(size * high)))
    else:
        stroke = 0
    mask = render_text(text, fonts.get(size), (255, 255, 255, 255), spacing, stroke)
    mask = vary_shape(mask, size)
    angle = random.uniform(-2, 2)
    mask = mask.rotate(angle, resample=Image.BICUBIC, expand=True)

    # 背景を作ってから、その明るさに合わせて文字色を決める
    background = make_background(mask.size, backgrounds).convert("RGBA")
    color = text_color_for(background)

    if random.random() < 0.3:  # ゲームの文字によくある影
        offset = random.randint(1, max(1, size // 16))
        shadow = Image.new("RGBA", mask.size, (0, 0, 0, 0))
        shadow.alpha_composite(colorize(mask, (0, 0, 0, 200)), (offset, offset))
        background.alpha_composite(shadow)

    background.alpha_composite(colorize(mask, color))
    return degrade(background.convert("RGB"))


def save_preview(images: list[Image.Image], path: Path):
    """最初の数枚を縦に並べた確認用の画像を作る"""
    width = max(img.width for img in images)
    height = sum(img.height for img in images) + 6 * len(images)
    sheet = Image.new("RGB", (width, height), (128, 128, 128))
    y = 0
    for img in images:
        sheet.paste(img, (0, y))
        y += img.height + 6
    sheet.save(path)


# ---------------------------------------------------------------- メイン
def main():
    parser = argparse.ArgumentParser(description="スタレ文字の合成学習データを作ります")
    parser.add_argument("--count", type=int, default=5000, help="作る枚数")
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT, help="スタレ文字フォントの場所")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="保存先フォルダ")
    parser.add_argument("--seed", type=int, default=None, help="同じ結果を再現したいときの乱数の種")
    parser.add_argument(
        "--bold", choices=list(BOLD_PROFILES), default="light",
        help="線を太くする変形の強さ（フォントの線がゲームより細いときは heavy）",
    )
    args = parser.parse_args()

    global bold_profile
    bold_profile = BOLD_PROFILES[args.bold]

    if not args.font.exists():
        raise SystemExit(f"フォントが見つかりません: {args.font}")

    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)

    fonts = Fonts(args.font)
    if fonts.latin_path is None:
        print("注意: 数字・記号用のフォントが見つからないため、簡易フォントで代用します")

    backgrounds = sorted(
        p for p in BACKGROUND_DIR.glob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ) if BACKGROUND_DIR.exists() else []
    corpus = load_corpus(CORPUS_FILE)
    print(f"背景画像: {len(backgrounds)}枚 / corpus.txt: {len(corpus)}行")

    args.output.mkdir(parents=True, exist_ok=True)
    for old in list(args.output.glob("*.png")) + list(args.output.glob("*.gt.txt")):
        old.unlink()  # 前回の生成結果を消してから作り直す

    preview = []
    for i in range(args.count):
        text = make_text(corpus)
        img = make_sample(text, fonts, backgrounds)
        name = f"{i:06d}"
        img.save(args.output / f"{name}.png")
        (args.output / f"{name}.gt.txt").write_text(text + "\n", encoding="utf-8", newline="\n")
        if len(preview) < 15:
            preview.append(img)
        if (i + 1) % 500 == 0:
            print(f"{i + 1} / {args.count} 枚")

    save_preview(preview, args.output.parent / "preview.png")
    print(f"完了: {args.output} に {args.count} 組を保存しました")
    print(f"確認用の画像: {args.output.parent / 'preview.png'}")


if __name__ == "__main__":
    main()