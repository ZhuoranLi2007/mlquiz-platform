"""Compose unaltered browser screenshots for visual before/after review."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out = Path(__file__).resolve().parent / 'screenshots'
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 22)
for suffix, before, after in (
    ('configuration', 'before_A01_ai_configuration.png', 'after_01_configuration.png'),
    ('candidates', 'before_A03_ai_candidates.png', 'after_02_candidates.png'),
    ('approved_detail', 'before_A08_approved_question_detail.png', 'after_04_approved.png'),
):
    canvas = Image.new('RGB', (2732, 808), '#ffffff')
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 8), '优化前', font=font, fill='#334155')
    draw.text((1386, 8), '优化后', font=font, fill='#334155')
    with Image.open(out / before) as old, Image.open(out / after) as new:
        canvas.paste(old.convert('RGB'), (0, 40))
        canvas.paste(new.convert('RGB'), (1366, 40))
    canvas.save(out / f'comparison_{suffix}.png')
