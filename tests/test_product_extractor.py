"""Milestone 7 tests for the saliency-based product extractor.

Uses the same two scenarios worked out interactively while tuning the
confidence metric (see the docstring/comments in saliency_extractor.py):
a real product photo pasted into a mock "screenshot" full of UI clutter,
and an already-tight product photo that should be left alone.
"""

from PIL import Image, ImageDraw

from product_extractor import SaliencyProductExtractor

GARMENT_PHOTO = (
    __file__.rsplit("tests", 1)[0] + "ai/vendor/fashn-vton-1.5/examples/data/garment.webp"
)


def _mock_screenshot(product: Image.Image, paste_xy=(250, 150)) -> Image.Image:
    canvas = Image.new("RGB", (900, 1300), color=(250, 250, 250))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, 900, 70], fill=(20, 20, 20))  # nav bar
    for yy in range(20, 60, 15):
        draw.rectangle([700, yy, 850, yy + 8], fill=(230, 230, 230))
    canvas.paste(product, paste_xy)
    for i, yy in enumerate(range(680, 760, 20)):
        draw.rectangle([250, yy, 250 + (300 if i else 500), yy + 10], fill=(180, 180, 180))
    draw.rectangle([250, 800, 500, 850], fill=(90, 90, 220))  # "Add to cart"
    draw.rectangle([0, 1200, 900, 1300], fill=(235, 235, 235))  # footer
    for xx in range(50, 850, 150):
        draw.rectangle([xx, 1230, xx + 100, 1250], fill=(200, 200, 200))
    return canvas


def _real_product_photo() -> Image.Image:
    return Image.open(GARMENT_PHOTO).convert("RGB").resize((400, 500))


def test_extracts_product_region_from_mock_screenshot():
    product = _real_product_photo()
    paste_xy = (250, 150)
    screenshot = _mock_screenshot(product, paste_xy)

    result = SaliencyProductExtractor().extract(screenshot)

    assert result.applied is True
    assert result.confidence > 0.5
    assert result.bounding_box is not None
    left, top, right, bottom = result.bounding_box
    # Crop should land close to where the product was actually pasted —
    # "close" allowing for the extractor's own padding margin.
    px, py = paste_xy
    assert abs(left - px) < 80
    assert abs(top - py) < 80
    assert abs(right - (px + product.width)) < 80
    assert abs(bottom - (py + product.height)) < 80
    # The result image should actually be a crop, not the untouched original.
    assert result.image.size != screenshot.size


def test_leaves_already_clean_product_photo_unchanged():
    product = _real_product_photo()

    result = SaliencyProductExtractor().extract(product)

    assert result.applied is False
    assert result.image.size == product.size
    assert result.bounding_box is None


def test_handles_blank_image_without_crashing():
    blank = Image.new("RGB", (500, 500), color=(255, 255, 255))

    result = SaliencyProductExtractor().extract(blank)

    # No meaningful saliency signal in a flat image — must not crash, and
    # must not fabricate a confident crop out of noise.
    assert result.applied is False
    assert result.image.size == blank.size
