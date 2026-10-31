"""Where a bill's coloured headers put its blocks: payment, user, charges and so on."""

from __future__ import annotations

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps


class Blocks:
    @classmethod
    def _high_voltage_block_bounds(
        cls, image: Image.Image
    ) -> dict[str, tuple[int, int, int, int]]:
        """Locate blocks from the repeated pink title-bar geometry."""
        regions = cls._pink_regions(image)
        tolerance = max(5, round(image.height * 0.006))
        rows: list[list[tuple[int, int, int, int]]] = []
        for region in sorted(regions, key=lambda item: (item[1], item[0])):
            if region[2] - region[0] < image.width * 0.12:
                continue
            row = next(
                (items for items in rows if abs(items[0][1] - region[1]) <= tolerance),
                None,
            )
            if row is None:
                rows.append([region])
            else:
                row.append(region)
                row.sort(key=lambda item: item[0])

        summary = next((row for row in rows if len(row) >= 4), None)
        if summary is None:
            return {}
        main = next(
            (row for row in rows if len(row) == 2 and row[0][1] > summary[0][1]),
            None,
        )
        if main is None:
            return {}
        invoice = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and row[0][1] > main[0][1]
                and row[0][0] < image.width * 0.2
            ),
            None,
        )
        content_bottom = invoice[1] if invoice else round(image.height * 0.78)
        other = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and main[0][1] < row[0][1] < content_bottom
                and row[0][0] > image.width * 0.6
            ),
            None,
        )

        bounds = {
            "payment_info": (
                summary[0][0],
                summary[0][1],
                summary[2][2],
                main[0][1],
            ),
            "user_info": (main[0][0], main[0][1], main[0][2], content_bottom),
            "charge_info": (main[1][0], main[1][1], main[1][2], content_bottom),
        }
        if other:
            bounds["other_info"] = (other[0], other[1], other[2], content_bottom)
        if invoice:
            bounds["invoice_info"] = (
                invoice[0],
                invoice[1],
                invoice[2],
                round(image.height * 0.94),
            )
        return bounds

    @classmethod
    def _bank_autopay_block_bounds(
        cls, image: Image.Image
    ) -> dict[str, tuple[int, int, int, int]]:
        """Locate blocks on a pink financial-institution payment receipt."""
        regions = cls._pink_regions(image)
        tolerance = max(5, round(image.height * 0.006))
        rows: list[list[tuple[int, int, int, int]]] = []
        for region in sorted(regions, key=lambda item: (item[1], item[0])):
            row = next(
                (items for items in rows if abs(items[0][1] - region[1]) <= tolerance),
                None,
            )
            if row is None:
                rows.append([region])
            else:
                row.append(region)
                row.sort(key=lambda item: item[0])

        summary = next(
            (
                row
                for row in rows
                if len([r for r in row if r[2] - r[0] > image.width * 0.12]) >= 4
                and row[0][1] < image.height * 0.35
            ),
            None,
        )
        content_row = next(
            (
                row
                for row in rows
                if summary is not None
                and row[0][1] > summary[0][1]
                and row[0][1] < image.height * 0.45
                and any(
                    image.width * 0.30 < item[0] < image.width * 0.48
                    and item[2] > image.width * 0.62
                    for item in row
                )
            ),
            None,
        )
        bottom_row = next(
            (
                row
                for row in rows
                if row[0][1] > image.height * 0.70
                and any(item[0] < image.width * 0.05 for item in row)
                and any(item[2] > image.width * 0.80 for item in row)
            ),
            None,
        )
        if summary is None or content_row is None or bottom_row is None:
            return {}

        charge_header = next(
            item
            for item in content_row
            if image.width * 0.30 < item[0] < image.width * 0.48
            and item[2] > image.width * 0.62
        )
        content_top = content_row[0][1]
        content_bottom = bottom_row[0][1]
        user_left = min(item[0] for item in content_row)
        user_right = max(
            item[2] for item in content_row if item[2] < charge_header[0]
        )
        other_headers = [
            item
            for row in rows
            for item in row
            if content_top < item[1] < content_bottom
            and item[0] > image.width * 0.65
        ]
        if not other_headers:
            return {}
        other_header = min(other_headers, key=lambda item: item[1])

        summary_regions = [
            item for item in summary if item[2] - item[0] > image.width * 0.12
        ]
        bounds = {
            "payment_info": (
                summary_regions[0][0],
                summary_regions[0][1],
                summary_regions[2][2],
                content_top,
            ),
            "user_info": (user_left, content_top, user_right, content_bottom),
            "charge_info": (
                charge_header[0], content_top, charge_header[2], content_bottom
            ),
            "other_info": (
                other_header[0], other_header[1], other_header[2], content_bottom
            ),
            "invoice_info": (
                min(item[0] for item in bottom_row),
                content_bottom,
                round(image.width * 0.48),
                round(image.height * 0.97),
            ),
            "carrier_info": (
                round(image.width * 0.485),
                content_bottom,
                max(item[2] for item in bottom_row),
                round(image.height * 0.97),
            ),
        }
        return {
            key: value
            for key, value in bounds.items()
            if value[2] > value[0] and value[3] > value[1]
        }

    @classmethod
    def _high_voltage_back_block_bounds(
        cls, image: Image.Image
    ) -> dict[str, tuple[int, int, int, int]]:
        """Locate the three useful blocks on the high-voltage bill back page."""
        regions = [
            item
            for item in cls._pink_regions(image)
            if item[2] - item[0] >= image.width * 0.35
        ]
        tolerance = max(5, round(image.height * 0.006))
        rows: list[list[tuple[int, int, int, int]]] = []
        for region in sorted(regions, key=lambda item: (item[1], item[0])):
            row = next(
                (items for items in rows if abs(items[0][1] - region[1]) <= tolerance),
                None,
            )
            if row is None:
                rows.append([region])
            else:
                row.append(region)
                row.sort(key=lambda item: item[0])

        top_pair = next((row for row in rows if len(row) == 2), None)
        if top_pair is None:
            return {}
        lower_left = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and row[0][1] > top_pair[0][1]
                and row[0][0] < image.width * 0.2
                and row[0][2] < image.width * 0.65
            ),
            None,
        )
        bottom_wide = next(
            (
                row[0]
                for row in rows
                if len(row) == 1
                and lower_left is not None
                and row[0][1] > lower_left[1]
                and row[0][2] - row[0][0] > image.width * 0.75
            ),
            None,
        )
        if lower_left is None or bottom_wide is None:
            return {}

        left, right = top_pair
        return {
            "charge_introduction": (left[0], left[1], left[2], lower_left[1]),
            "energy_saving_info": (right[0], right[1], right[2], bottom_wide[1]),
            "meter_info": (
                lower_left[0],
                lower_left[1],
                lower_left[2],
                bottom_wide[1],
            ),
        }

    @staticmethod
    def _pink_mask(image: Image.Image) -> Image.Image:
        hue, saturation, value = image.convert("HSV").split()
        pink_hue = hue.point(lambda item: 255 if 185 <= item <= 252 else 0)
        pink_saturation = saturation.point(lambda item: 255 if item >= 24 else 0)
        visible = value.point(lambda item: 255 if item >= 130 else 0)
        return ImageChops.multiply(
            ImageChops.multiply(pink_hue, pink_saturation), visible
        )

    @staticmethod
    def _colored_mask(image: Image.Image) -> Image.Image:
        _, saturation, value = image.convert("HSV").split()
        saturated = saturation.point(lambda item: 255 if item >= 35 else 0)
        visible_ink = value.point(lambda item: 255 if 60 <= item <= 250 else 0)
        return ImageChops.multiply(saturated, visible_ink)

    @classmethod
    def _pink_regions(cls, image: Image.Image) -> list[tuple[int, int, int, int]]:
        mask = cls._pink_mask(image)
        pixels = mask.load()

        def runs(flags: list[bool], allowed_gap: int = 3) -> list[tuple[int, int]]:
            found: list[tuple[int, int]] = []
            start = None
            last_true = None
            for index, active in enumerate(flags):
                if active:
                    if start is None:
                        start = index
                    last_true = index
                elif start is not None and index - last_true > allowed_gap:
                    found.append((start, last_true + 1))
                    start = None
                    last_true = None
            if start is not None:
                found.append((start, last_true + 1))
            return found

        row_threshold = max(8, image.width // 100)
        active_rows = [
            sum(1 for x in range(image.width) if pixels[x, y]) >= row_threshold
            for y in range(image.height)
        ]
        regions: list[tuple[int, int, int, int]] = []
        for top, bottom in runs(active_rows):
            if bottom - top < 4:
                continue
            active_columns = [
                sum(1 for y in range(top, bottom) if pixels[x, y]) >= 2
                for x in range(image.width)
            ]
            for left, right in runs(active_columns):
                if right - left >= 35:
                    regions.append((left, top, right, bottom))
        return regions
