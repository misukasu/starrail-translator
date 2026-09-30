"""Illustratorで書き出したSVG（upper_A.svg 〜 lower_z.svg）から、フォントを作るスクリプト

FontForgeに付属しているPythonで実行します（普通のPythonでは動きません）。
Windowsのコマンドプロンプトで、次のように実行します（FontForgeのインストール先に合わせて変えてください）:

    "C:\\Program Files\\FontForgeBuilds\\bin\\fontforge.exe" -script build_font.py

できるもの:
    fonts/starrail-original.ttf  … アプリや学習データ作りで使うフォント
    fonts/starrail-original.sfd  … FontForgeで開いて、手で微調整するための元データ
"""

import glob
import os
import re
import statistics
import sys
import tempfile

import fontforge
import psMat

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, "..", "fonts", "svg")
OUTPUT_TTF = os.path.join(HERE, "..", "fonts", "starrail-original.ttf")
OUTPUT_SFD = os.path.join(HERE, "..", "fonts", "starrail-original.sfd")

FAMILY_NAME = "StarRail Original"
FONT_NAME = "StarRailOriginal"
VERSION = "0.1"

SIDE_BEARING = 50  # 文字の左右に付ける余白（フォントの単位。文字の高さ全体が1000）
SPACE_WIDTH = 300  # スペースの幅
# ベースラインより下に伸びる文字（ベースラインの判定から除く）
# スタレ文字では英語の g j p q y ではなく、この7つが下に伸びる。大文字も伸びる場合は、ここに追加する
DESCENDERS = set("bdfklvw")


# SVGに書かれた「線（stroke）」の設定。フォントは塗りの形だけを使うので、読み込む前に取り除く
STROKE_ATTRIBUTE = re.compile(r'\s(?:stroke(?:-[a-z]+)?)="[^"]*"')
STROKE_IN_STYLE = re.compile(r'stroke(?:-[a-z]+)?\s*:[^;"]*;?')


def load_svg_without_stroke(path):
    """線の設定を取り除いたSVGを一時ファイルに書き出し、そのファイルの場所と、線があったかどうかを返す"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    cleaned = STROKE_IN_STYLE.sub("", STROKE_ATTRIBUTE.sub("", text))
    temp = tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8")
    temp.write(cleaned)
    temp.close()
    return temp.name, cleaned != text


def char_from_name(stem):
    """upper_A → "A"、lower_a → "a" のように、ファイル名から文字を取り出す"""
    match = re.fullmatch(r"(upper|lower)_([A-Za-z])", stem)
    if not match:
        return None
    kind, letter = match.groups()
    return letter.upper() if kind == "upper" else letter.lower()


def main():
    # 画面が日本語を表示できない環境でも、エラーで止まらないようにする
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    font = fontforge.font()
    font.encoding = "UnicodeFull"
    font.em = 1000
    font.ascent = 800
    font.descent = 200
    font.familyname = FAMILY_NAME
    font.fontname = FONT_NAME
    font.fullname = FAMILY_NAME
    font.version = VERSION

    # ---- SVGを1文字ずつ読み込む ----
    glyphs = {}
    for path in sorted(glob.glob(os.path.join(SVG_DIR, "*.svg"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        ch = char_from_name(stem)
        if ch is None:
            print(f"ファイル名から文字がわからないのでスキップ: {os.path.basename(path)}")
            continue
        glyph = font.createChar(ord(ch))
        cleaned_path, had_stroke = load_svg_without_stroke(path)
        try:
            glyph.importOutlines(cleaned_path)
        finally:
            os.remove(cleaned_path)
        if had_stroke:
            print(f"注意: {os.path.basename(path)} に線（stroke）が付いていたので、線を無視して読み込みました")
        glyph.removeOverlap()  # 重なった部品を1つにまとめる
        glyph.correctDirection()  # 輪郭の向きを直して、穴が正しく抜けるようにする
        if glyph.boundingBox() == (0, 0, 0, 0):
            print(f"注意: {os.path.basename(path)} に形がありません（塗りが無い・線だけなど）")
        glyphs[ch] = glyph

    if not glyphs:
        raise SystemExit(f"SVGが見つかりませんでした: {os.path.abspath(SVG_DIR)}")

    # ---- ベースラインをそろえる ----
    # 下に伸びない文字の「一番下」の位置の中央値を、ベースライン（高さ0）とみなす
    bottoms = [g.boundingBox()[1] for ch, g in glyphs.items() if ch not in DESCENDERS]
    baseline = statistics.median(bottoms) if bottoms else 0
    for glyph in glyphs.values():
        glyph.transform(psMat.translate(0, -baseline))

    # ---- 左右の余白と、スペースの幅を決める ----
    for glyph in glyphs.values():
        glyph.left_side_bearing = SIDE_BEARING
        glyph.right_side_bearing = SIDE_BEARING
    space = font.createChar(32)
    space.width = SPACE_WIDTH

    # ---- 結果の報告 ----
    def median_top(chars):
        tops = [glyphs[c].boundingBox()[3] for c in chars if c in glyphs]
        return round(statistics.median(tops)) if tops else None

    lower_plain = [c for c in "acemnorsuxz"]  # 小文字の高さを測るのに使う文字
    upper_all = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    print(f"読み込んだ文字: {len(glyphs)} / 52")
    missing = [c for c in upper_all + [c.lower() for c in upper_all] if c not in glyphs]
    if missing:
        print(f"足りない文字: {' '.join(missing)}")
    print(f"大文字の高さ（中央値）: {median_top(upper_all)}")
    print(f"小文字の高さ（中央値）: {median_top(lower_plain)}")
    too_tall = [ch for ch, g in glyphs.items() if g.boundingBox()[3] > font.ascent]
    too_low = [ch for ch, g in glyphs.items() if g.boundingBox()[1] < -font.descent]
    if too_tall:
        print(f"注意: 上にはみ出している文字: {' '.join(too_tall)}")
    if too_low:
        print(f"注意: 下にはみ出している文字: {' '.join(too_low)}")

    os.makedirs(os.path.dirname(OUTPUT_TTF), exist_ok=True)
    font.save(OUTPUT_SFD)
    font.generate(OUTPUT_TTF)
    print(f"完成: {os.path.abspath(OUTPUT_TTF)}")


main()
