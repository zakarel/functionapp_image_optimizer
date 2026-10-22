import unittest
import os

from function_app import (
    IMAGE_NAME_PATTERN,
    ImageMagickConversionError,
    InvalidTransformError,
    TransformOptions,
    _build_transform_args,
    _crop_geometry,
    _detect_image_format,
    _parse_options,
    _source_blob_name,
    _variant_key,
)


class ParseOptionsTests(unittest.TestCase):
    def test_defaults_match_optimized_delivery(self):
        options = _parse_options({})

        self.assertEqual(options.output_format, "webp")
        self.assertEqual(options.quality, 85)
        self.assertEqual(options.fit, "bounds")
        self.assertEqual(options.orient, "auto")

    def test_dpr_scales_requested_dimensions(self):
        options = _parse_options({"width": "320", "height": "180", "dpr": "2"})

        self.assertEqual(options.width, 640)
        self.assertEqual(options.height, 360)

    def test_cover_requires_both_dimensions(self):
        with self.assertRaisesRegex(InvalidTransformError, "requires both"):
            _parse_options({"width": "300", "fit": "cover"})

    def test_unknown_parameter_is_rejected(self):
        with self.assertRaisesRegex(InvalidTransformError, "Unsupported parameter"):
            _parse_options({"width": "300", "surprise": "true"})

    def test_output_limit_is_enforced_after_dpr(self):
        with self.assertRaisesRegex(InvalidTransformError, "exceeds 4096"):
            _parse_options({"width": "3000", "dpr": "2"})

    def test_nested_jpeg_and_png_paths_are_supported(self):
        self.assertTrue(IMAGE_NAME_PATTERN.fullmatch("2026/10/photo.jpg"))
        self.assertTrue(IMAGE_NAME_PATTERN.fullmatch("2026/10/chart.png"))
        self.assertFalse(IMAGE_NAME_PATTERN.fullmatch("../photo.jpg"))
        self.assertEqual(_source_blob_name("2026/10/photo.jpg"), "2026/10/photo.jpg")

    def test_source_prefix_is_applied_without_path_traversal(self):
        previous = os.environ.get("SOURCE_PATH_PREFIX")
        os.environ["SOURCE_PATH_PREFIX"] = "wordpress/uploads/"
        try:
            self.assertEqual(
                _source_blob_name("2026/10/photo.png"),
                "wordpress/uploads/2026/10/photo.png",
            )
        finally:
            if previous is None:
                os.environ.pop("SOURCE_PATH_PREFIX", None)
            else:
                os.environ["SOURCE_PATH_PREFIX"] = previous


class ImageMagickCommandTests(unittest.TestCase):
    def test_cover_builds_resize_crop_sequence(self):
        options = _parse_options(
            {
                "width": "640",
                "height": "360",
                "fit": "cover",
                "crop": "16:9,center",
            }
        )

        args = _build_transform_args(options, (1200, 900))

        self.assertIn("640x360^", args)
        self.assertIn("-extent", args)
        self.assertIn("Center", args)

    def test_crop_derives_missing_dimension_and_forces_crop_fit(self):
        options = _parse_options({"width": "300", "crop": "16:9"})

        self.assertEqual(options.width, 300)
        self.assertEqual(options.height, 169)
        self.assertEqual(options.fit, "crop")
        args = _build_transform_args(options, (1200, 900))
        self.assertIn("300x169^", args)
        self.assertIn("-extent", args)

    def test_filters_are_bounded_and_emitted(self):
        options = _parse_options(
            {
                "brightness": "8",
                "contrast": "12",
                "saturation": "90",
                "sharpen": "1.2",
                "blur": "0.5",
                "bw": "true",
            }
        )

        args = _build_transform_args(options, (1200, 900))

        self.assertIn("-colorspace", args)
        self.assertIn("8x12", args)
        self.assertIn("100,90,100", args)
        self.assertIn("0x1.2", args)
        self.assertIn("0x0.5", args)

    def test_crop_only_preserves_largest_requested_ratio(self):
        self.assertEqual(_crop_geometry(1200, 900, (16, 9)), (1200, 675))
        self.assertEqual(_crop_geometry(900, 1200, (1, 1)), (900, 900))


class VariantTests(unittest.TestCase):
    def test_variant_key_changes_with_source_or_transform(self):
        base = TransformOptions(width=400)
        different = TransformOptions(width=800)

        first = _variant_key("photo.jpg", '"etag-1"', base)
        changed_source = _variant_key("photo.jpg", '"etag-2"', base)
        changed_transform = _variant_key("photo.jpg", '"etag-1"', different)

        self.assertNotEqual(first, changed_source)
        self.assertNotEqual(first, changed_transform)
        self.assertTrue(first.startswith("variants/photo/"))
        self.assertTrue(first.endswith(".webp"))


class SourceFormatTests(unittest.TestCase):
    def test_jpeg_and_png_signatures_are_detected(self):
        self.assertEqual(_detect_image_format(b"\xff\xd8\xffrest"), "jpeg")
        self.assertEqual(
            _detect_image_format(b"\x89PNG\r\n\x1a\nrest"),
            "png",
        )

    def test_unsupported_signature_is_rejected(self):
        with self.assertRaisesRegex(ImageMagickConversionError, "supported JPEG or PNG"):
            _detect_image_format(b"GIF89a")


class FunctionRouteTests(unittest.TestCase):
    def test_only_get_delivery_and_health_routes_are_registered(self):
        from function_app import app

        routes = {function._trigger.route: function._trigger.methods for function in app.get_functions()}
        self.assertEqual(set(routes), {"io/{*name}", "health"})
        self.assertEqual({method.value for method in routes["io/{*name}"]}, {"GET", "HEAD"})


if __name__ == "__main__":
    unittest.main()
