from unittest import TestCase
from unittest import mock

from utils import at_wiki


class VocaloidCollectionInfoTest(TestCase):
    @mock.patch.object(at_wiki, "find_at_wiki_page", return_value="https://example.test/song")
    @mock.patch.object(at_wiki, "http_get")
    def test_returns_top30_track_and_rank(self, mock_http_get, _mock_find_page):
        response = mock_http_get.return_value
        response.text = "ボカコレ2020冬 TOP30ランキング第30位"

        self.assertEqual(
            ("ボカコレ2020冬", "TOP30", "30"),
            at_wiki.get_vocaloid_collection_info("ネリネージュ"))
        response.raise_for_status.assert_called_once_with()
