"""Path map between the person's folder and the desktop container's data folder."""

from __future__ import annotations

import asyncio

import pytest
from mcp import Client

from deckwright import hostpaths, images, mcp_server, pack

HOST = "/Users/sam/Deckwright"


@pytest.fixture
def mapped(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "Inbox").mkdir(parents=True)
    monkeypatch.setenv("DECKWRIGHT_DATA_DIR", str(data))
    monkeypatch.setenv("DECKWRIGHT_HOST_DIR", HOST)
    return data.resolve()


def test_paths_pass_through_without_host_dir(monkeypatch):
    monkeypatch.delenv("DECKWRIGHT_HOST_DIR", raising=False)
    assert hostpaths.to_container("/anywhere/x.pptx") == "/anywhere/x.pptx"
    assert hostpaths.to_host({"path": "/data/x.pptx"}) == {"path": "/data/x.pptx"}


def test_host_path_maps_into_the_container(mapped):
    assert hostpaths.to_container(f"{HOST}/Inbox/acme.pptx") == str(mapped / "Inbox" / "acme.pptx")
    assert hostpaths.to_container(HOST) == str(mapped)


def test_container_path_is_accepted(mapped):
    assert hostpaths.to_container(str(mapped / "Inbox" / "a.png")) == str(mapped / "Inbox" / "a.png")


def test_relative_path_is_inside_the_folder(mapped):
    assert hostpaths.to_container("Inbox/acme.pptx") == str(mapped / "Inbox" / "acme.pptx")
    with pytest.raises(hostpaths.HostPathError):
        hostpaths.to_container("../secret.txt")


@pytest.mark.parametrize("path", ["/Users/sam/Downloads/acme.pptx", "/etc/passwd",
                                  "/Users/sam/DeckwrightOther/a.pptx"])
def test_paths_outside_the_folder_are_refused(mapped, path):
    with pytest.raises(hostpaths.HostPathError, match="outside your Deckwright folder"):
        hostpaths.to_container(path)


def test_dot_dot_is_refused(mapped):
    with pytest.raises(hostpaths.HostPathError):
        hostpaths.to_container(f"{HOST}/Inbox/../../etc/passwd")
    with pytest.raises(hostpaths.HostPathError):
        hostpaths.to_container(str(mapped / ".." / "secret"))


def test_symlink_out_of_the_folder_is_refused(mapped, tmp_path):
    (tmp_path / "secret.txt").write_text("x")
    (mapped / "Inbox" / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(hostpaths.HostPathError):
        hostpaths.to_container(f"{HOST}/Inbox/link.txt")


def test_results_show_host_paths(mapped):
    result = {"path": str(mapped / "Decks" / "a.pptx"), "diagrams": [str(mapped / "Decks" / "d.excalidraw")],
              "other": "/datafile", "n": 3, "root": str(mapped)}
    assert hostpaths.to_host(result) == {"path": f"{HOST}/Decks/a.pptx", "diagrams": [f"{HOST}/Decks/d.excalidraw"],
                                         "other": "/datafile", "n": 3, "root": HOST}
    # A sibling folder that only shares the prefix is not mapped.
    assert hostpaths.to_host(f"{mapped}x/a.pptx") == f"{mapped}x/a.pptx"


def test_windows_host_folder(mapped, monkeypatch):
    monkeypatch.setenv("DECKWRIGHT_HOST_DIR", r"C:\Users\Sam\Deckwright")
    assert hostpaths.to_container(r"c:\users\sam\deckwright\Inbox\acme.pptx") == str(mapped / "Inbox" / "acme.pptx")
    assert hostpaths.to_container(r"Inbox\acme.pptx") == str(mapped / "Inbox" / "acme.pptx")
    assert hostpaths.to_host(str(mapped / "Decks" / "a.pptx")) == r"C:\Users\Sam\Deckwright\Decks\a.pptx"
    with pytest.raises(hostpaths.HostPathError):
        hostpaths.to_container(r"C:\Users\Sam\Downloads\acme.pptx")


def test_local_images_are_mapped_and_limited(mapped):
    assert images.check_local_path(f"{HOST}/Inbox/a.png", allow_local=True) == mapped / "Inbox" / "a.png"
    with pytest.raises(PermissionError):
        images.check_local_path("/Users/sam/Pictures/a.png", allow_local=True)


def test_packs_dir_follows_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKWRIGHT_PACKS_DIR", str(tmp_path / "Templates"))
    assert pack.packs_dir() == tmp_path / "Templates"
    assert tmp_path / "Templates" in pack.search_paths()
    monkeypatch.delenv("DECKWRIGHT_PACKS_DIR")
    assert pack.packs_dir() == pack.CONFIG_DIR / "templates"


def test_mcp_returns_host_paths_and_says_where_files_go(output_dir, monkeypatch):
    monkeypatch.setenv("DECKWRIGHT_DATA_DIR", str(output_dir))
    monkeypatch.setenv("DECKWRIGHT_HOST_DIR", HOST)
    server = mcp_server.create()
    spec = {"title": "T", "slides": [{"layout": "title", "title": "Hi"}, {"layout": "closing"}]}

    async def run():
        async with Client(server) as c:
            built = await c.call_tool("create_presentation", {"spec": spec})
            refused = await c.call_tool("add_template", {"pptx_path": "/Users/sam/Downloads/a.pptx",
                                                         "template_id": "acme"})
            return c.instructions, built.structured_content, refused

    instructions, built, refused = asyncio.run(run())
    assert f"Deckwright folder, {HOST}" in instructions
    assert built["path"].startswith(f"{HOST}/") and built["path"].endswith(".pptx")
    assert refused.is_error and "outside your Deckwright folder" in refused.content[0].text


def test_tilde_and_case_on_macos(mapped, monkeypatch):
    monkeypatch.setenv("DECKWRIGHT_HOST_OS", "darwin")
    monkeypatch.setenv("DECKWRIGHT_HOST_HOME", "/Users/sam")
    assert hostpaths.to_container("~/Deckwright/Inbox/a.pptx") == str(mapped / "Inbox" / "a.pptx")
    assert hostpaths.to_container("/users/SAM/deckwright/Inbox/a.pptx") == str(mapped / "Inbox" / "a.pptx")
    with pytest.raises(hostpaths.HostPathError):
        hostpaths.to_container("~/Downloads/a.pptx")


def test_case_matters_on_linux(mapped, monkeypatch):
    monkeypatch.setenv("DECKWRIGHT_HOST_OS", "linux")
    with pytest.raises(hostpaths.HostPathError):
        hostpaths.to_container("/users/sam/deckwright/Inbox/a.pptx")


def test_backslash_in_a_macos_folder_is_not_windows(mapped, monkeypatch):
    monkeypatch.setenv("DECKWRIGHT_HOST_OS", "darwin")
    monkeypatch.setenv("DECKWRIGHT_HOST_DIR", "/Users/sam/My\\Decks")
    assert hostpaths.to_container("/Users/sam/My\\Decks/Inbox/a.pptx") == str(mapped / "Inbox" / "a.pptx")
    assert hostpaths.to_host(str(mapped / "Decks" / "a.pptx")) == "/Users/sam/My\\Decks/Decks/a.pptx"


def test_paths_inside_messages_are_mapped(mapped):
    warning = f"cannot read {mapped}/Inbox/logo.png: not an image; also {mapped}-old/x and {mapped}x"
    assert hostpaths.to_host([warning]) == [f"cannot read {HOST}/Inbox/logo.png: not an image; also "
                                            f"{mapped}-old/x and {mapped}x"]


def test_old_packs_folder_stays_found(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKWRIGHT_PACKS_DIR", str(tmp_path / "Templates"))
    paths = pack.search_paths()
    assert paths.index(tmp_path / "Templates") < paths.index(pack.CONFIG_DIR / "templates")


def test_windows_paths_inside_messages(mapped, monkeypatch):
    monkeypatch.setenv("DECKWRIGHT_HOST_DIR", r"C:\Users\Sam\Deckwright")
    assert (hostpaths.to_host(f"cannot read {mapped}/Inbox/logo.png: bad")
            == r"cannot read C:\Users\Sam\Deckwright\Inbox\logo.png: bad")


def test_urls_and_longer_paths_are_not_rewritten(mapped):
    text = f"see https://example.com{mapped}/img.png and /srv{mapped}/x; real: {mapped}/Decks/a.pptx"
    assert hostpaths.to_host(text) == (f"see https://example.com{mapped}/img.png and /srv{mapped}/x; "
                                       f"real: {HOST}/Decks/a.pptx")
