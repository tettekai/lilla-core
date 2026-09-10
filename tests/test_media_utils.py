from lilla_core.utils.media_utils import MEDIA_FILES_PATH, MediaRef, build_media_ref


class TestBuildMediaRef:
    """build_media_ref のテスト。"""

    def test_returns_media_ref_namedtuple(self):
        """MediaRef（media_id / url）を返すこと。"""
        ref = build_media_ref("http://example.com", "gentle_smile_01.jpg")
        assert isinstance(ref, MediaRef)
        assert ref.media_id == "gentle_smile_01"
        assert ref.url == "http://example.com/media/files/gentle_smile_01.jpg"

    def test_media_id_strips_extension(self):
        """media_id は拡張子を除いたファイル名になること。"""
        ref = build_media_ref("http://example.com", "worried_01.png")
        assert ref.media_id == "worried_01"

    def test_url_uses_media_files_path(self):
        """URL に共通のメディア配信パスが含まれること。"""
        ref = build_media_ref("http://host:8080", "a.jpg")
        assert MEDIA_FILES_PATH in ref.url
        assert ref.url == f"http://host:8080{MEDIA_FILES_PATH}/a.jpg"

    def test_unpackable_as_tuple(self):
        """タプルとしてアンパックできること。"""
        media_id, url = build_media_ref("http://example.com", "img.jpg")
        assert media_id == "img"
        assert url == "http://example.com/media/files/img.jpg"

    def test_filename_without_extension(self):
        """拡張子のないファイル名でも media_id をそのまま扱えること。"""
        ref = build_media_ref("http://example.com", "noext")
        assert ref.media_id == "noext"
        assert ref.url == "http://example.com/media/files/noext"
