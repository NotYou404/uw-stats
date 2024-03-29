# Parts of this code are inspired by or copied from
# https://github.com/ifscript/lootscript.
# This notice is also found at the top of affected functions.

import copy
import datetime as dt
import math
import string
import sys
from pathlib import Path
from typing import Optional

import bs4
import dateparser
import pandas as pd
import regex as re
from emojis import is_emoji

WHITESPACES = r"\xad͏\u061cᅟᅠ឴឵\u180e\u2000\u2001\u2002\u2003\u2004\u2005"\
              r"\u2006"\
              r"\u2007\u2008\u2009\u200a\u200b\u200c\u200d\u200e\u200f \u205f"\
              r"\u2060\u2061\u2062\u2063\u2064\u206a\u206b\u206c\u206d\u206e"\
              r"\u206f\u3000\ufeff"\
              r"\ufe0f\ufe0f"\

# bs4 seems to recursively parse the html. Errors sometimes.
sys.setrecursionlimit(10_000)


def get_page_for_message(post_num: int) -> int:
    """Finds the page a given post should be in.

    Args:
        post_num (int): The post number.

    Returns:
        int: The page number.
    """
    return math.ceil(post_num / 20)


def get_first_post_from_page(page_num: int) -> int:
    """Returns the first post number on a given page.

    Args:
        page_num (int): The page number.

    Returns:
        int: The first post number.
    """
    return page_num * 20 - 19


def get_last_post_from_page(page_num: int) -> int:
    """Returns the last post number on a given page.

    Args:
        page_num (int): The page number.

    Returns:
        int: The last post number.
    """
    return page_num * 20


def find_all_messages(soup: bs4.BeautifulSoup) -> list[bs4.element.Tag]:
    """Returns a list of message article tags.

    Args:
        soup (bs4.BeautifulSoup): The BeautifulSoup object of the HTML page.

    Returns:
        list[bs4.element.Tag]: The list containing the message article tags.
    """
    return soup.find_all("article", class_="message")


def find_message_content(message: bs4.element.Tag) -> bs4.element.Tag:
    """Retrieves the content from a message.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        bs4.element.Tag: The message content's element tag object.
    """
    return message.find("div", class_="message-content")


def construct_dataframe(
    path: str | Path,
    pagerange: Optional[range] = None,
    postrange: Optional[range] = None,
) -> pd.DataFrame:
    """Constructs a dataframe pagewise from HTML files.
    This could eventually be split up into multiple functions or a class.
    Use the *range parameters to specify what pages to be included in the
    output. Second range argument is exclusive.

    Args:
        path (str | Path): The path containing the HTML files.
        pagerange (range, optional): The pagerange to include in the table.
        Mutually exclusive with other ranges. Defaults to None.
        postrange (range, optional): The postrange to include in the table.
        Mutually exclusive with other ranges. Defaults to None.

    Returns:
        pd.DataFrame: The newly created dataframe.
    """
    if all([pagerange, postrange]):
        raise ValueError("Only one *range parameter can be given.")

    columns = [
        "post_num",
        "page_num",
        "author",
        "creation_datetime",
        "content",
        "like_count",
        "quote_count",
        "quoted_list",
        "spoiler_count",
        "mentions_count",
        "mentioned_list",
        "word_count",
        "emoji_count",
        "emoji_frequency_mapping",
        "is_edited",
        "is_rules_compliant",
        "rulebreak_reasons",
    ]

    series_list = []
    for file in sorted(
        Path(path).iterdir(), key=lambda s: re.findall(r"\d+", s.name)[0]
    ):
        if file.is_dir():
            continue
        page_num = int(re.findall(r"\d+", file.name)[0])

        # range checks
        if pagerange:
            if page_num not in pagerange:
                continue
        if postrange:
            if (
                get_first_post_from_page(page_num) not in postrange and
                get_last_post_from_page(page_num) not in postrange
            ):
                continue

        print("Processing", file)
        soup = bs4.BeautifulSoup(
            file.read_text("utf-8"), features="html.parser"
        )

        for message in find_all_messages(soup):
            post_num = get_post_num(message)
            if postrange:
                if post_num not in postrange:
                    continue
            # Some data-gathering functions need access to otherwise
            # noisy tags.
            # Every function tries to access the unmodified message by
            # default, unless it needs to work with content text or raw
            # HTML. Or needs to modify the message.
            unmodified_message = copy.copy(message)
            content_tag = find_message_content(message)  # Can be modified

            author = unmodified_message["data-author"]
            creation_datetime = get_message_creation_time(unmodified_message)
            is_edited = has_edited_message(unmodified_message)

            quote_count = get_amount_of_quotes(unmodified_message)
            quoted_list = get_list_of_quoted_usernames(unmodified_message)

            spoiler_count = get_amount_of_spoilers(unmodified_message)

            mentioned_list = get_list_of_mentioned_usernames(
                unmodified_message
            )
            mentions_count = len(mentioned_list)

            # Must come before insert_dot_after_last_emoji()
            emoji_frequency_mapping = (
                get_mapping_of_emojis_and_frequency(unmodified_message)
            )
            emoji_count = sum(i for i in emoji_frequency_mapping.values())

            like_count = get_amount_of_likes(message)  # modifies

            clean_noisy_tags(message)  # modifies

            content = content_tag.get_text(strip=True)  # needs modified

            word_count = get_amount_of_words(content_tag)

            rules_compliance_check_result = rules_reworked(content)
            rulebreak_reasons = [
                k for k, v in rules_compliance_check_result.items() if not v
            ]
            is_rules_compliant = not rulebreak_reasons

            message_series = pd.Series(
                data=(
                    [
                        post_num,
                        page_num,
                        author,
                        creation_datetime,
                        content,
                        like_count,
                        quote_count,
                        quoted_list,
                        spoiler_count,
                        mentions_count,
                        mentioned_list,
                        word_count,
                        emoji_count,
                        emoji_frequency_mapping,
                        is_edited,
                        is_rules_compliant,
                        rulebreak_reasons,
                    ]
                ),
                index=columns,
            )
            series_list.append(message_series)

    if pagerange:
        index = range(
            get_first_post_from_page(pagerange[0]),
            # +1 due to the range stop value being exclusive
            get_last_post_from_page(pagerange[-1]) + 1,
        )
    elif postrange:
        index = postrange
    else:
        index = None

    df = pd.DataFrame(
        series_list,
        index=index,
        columns=columns,
        copy=False,
    )
    return df


def get_post_num(message: bs4.element.Tag) -> int:
    """Get the post number of a message.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        int: The post number.
    """
    postnum_str = message.find_all("li")[3].get_text(strip=True)[1:]
    return int(postnum_str.replace(".", ""))


def get_amount_of_likes(message: bs4.element.Tag) -> int:
    """Get the amount of likes a message has.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        int: The like count.
    """
    # Inspired by
    # https://github.com/ifscript/lootscript/blob/main/lootscript.py
    likes_bar = message.find("a", class_="reactionsBar-link")

    if likes_bar is None:
        # No likes found
        return 0

    num_likes = len(likes_bar.find_all('bdi'))

    if num_likes < 3:
        # Can be more if num_likes is 3
        return num_likes

    for bdi in likes_bar("bdi"):
        # Remove usernames
        bdi.decompose()

    text = likes_bar.get_text(strip=True)
    try:
        # Return additional likes plus the ones being counted
        return int(re.findall(r"\d+", text)[0]) + num_likes
    except IndexError:
        # There are just 3 likes
        return num_likes


def clean_noisy_tags(message: bs4.element.Tag) -> None:
    """Decomposes various hard-coded noisy Tags.

    Args:
        message (bs4.element.Tag): The message's element tag object.
    """
    # Turn emojis into their alt
    for emoji in message.find_all("img", class_="smilie"):
        try:
            emoji.insert_after(".")  # Use emojis as Sentence delimiter
            alt = emoji["alt"]
            emoji.insert_before(alt)
            emoji.decompose()
        except (TypeError, ValueError):
            # Rare error of a corrupted image tag (?)
            # Just ignoring, it's all @fscript's fault.
            pass

    # Media Tags have a noisy "Ansehen auf" string.
    for p in message.find_all("p"):
        if p.get_text(strip=True) == "Ansehen auf":
            p.decompose()

    # Tags whose content shouldn't be in the message content.
    # List of tuples. First tuple element is the tag string, second
    # an optional class.
    useless_tags: list[tuple[str, Optional[str]]] = [
        ("script", None),
        ("table", None),
        ("blockquote", None),
        ("div", "message-lastEdit")
    ]
    for tag, class_ in useless_tags:
        if class_:
            all_tags = message.find_all(tag, class_=class_)
        else:
            all_tags = message.find_all(tag)
        for find in all_tags:
            find.decompose()


def get_amount_of_quotes(message: bs4.element.Tag) -> int:
    """Retrieves the amount of quotes of a message.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        int: The quote count.
    """
    return len(message.find_all("blockquote", class_="bbCodeBlock--quote"))


def get_list_of_quoted_usernames(message: bs4.element.Tag) -> list[str]:
    """Retrieves a list of usernames being quoted in a message.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        list[str]: The list containing the usernames.
    """
    usernames: list[str] = []
    for quote in message.find_all("blockquote", class_="bbCodeBlock--quote"):
        usernames.append(quote["data-quote"])
    return usernames


def get_amount_of_spoilers(message: bs4.element.Tag) -> int:
    """Retrieves the amount of spoilers in a message.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        int: The spoiler count.
    """
    return len(message.find_all("div", class_="bbCodeSpoiler"))


def get_list_of_mentioned_usernames(message: bs4.element.Tag) -> list[str]:
    """Retrieves a list of mentioned usernames. Get the amount of mentions
    by using len() on the list.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        list[str]: The list containing all usernames.
    """
    usernames: list[str] = []
    for mention in message.find_all("a", class_="username"):
        if (uname := mention.get_text(strip=True))[0] == "@":
            # There can also be other anchor tags with that class.
            # However, only mentions start with @.
            # Perf. note: str[0]=="@" performs almost 3 times faster than
            # str.startswith("@").
            usernames.append(uname[1:])
    return usernames


def get_amount_of_words(content: bs4.element.Tag) -> int:
    """Retrieves the amount of words in a message.

    Args:
        content (bs4.element.Tag): The message content's element tag object.

    Returns:
        int: The word count.
    """
    return _count_words(content.get_text(strip=True))


def get_mapping_of_emojis_and_frequency(
    message: bs4.element.Tag
) -> dict[str, int]:
    """Returns a mapping from all occurring emojis to their frequency.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        dict[str, int]: A mapping from all occurring emojis to their frequency.
    """
    emojis: dict[str, int] = {}
    for emoji in message.find_all("img", class_="smilie"):
        alt = emoji["alt"]
        if not emojis.get(alt):
            emojis[alt] = 1
            continue
        emojis[alt] += 1
    return emojis


def _count_words(string_: str) -> int:
    """Counts the amount of words in a string by utilizing the
    str.split() method. Splits on any whitespace character and
    discards empty strings.

    Args:
        string (str): The string to count the words from.

    Returns:
        int: The amount of words in the string.
    """
    return len(re.split(rf"[\s{string.punctuation}]", string_))


def has_edited_message(message: bs4.element.Tag) -> bool:
    """Check if the message has been edited at least once.

    Args:
        message (bs4.element.Tag): The message's element tag object.

    Returns:
        bool: Wether or not the message has been edited.
    """
    if message.find("div", class_="message-lastEdit"):
        return True
    return False


def check_rules_compliance(
    content: str, word_count_: int
) -> tuple[bool, list[Optional[str]]]:
    """
    DEPRECATED - USE rules_reworked()
    Checks if a post is compliant to the rules.

    Args:
        content (str): The cleaned up and stripped content string.
        word_count (int): The word count.

    Returns:
        tuple[bool, list[Optional[str], ...]]: A tuple with the first
        element being the check result and the second one being a list
        with the reasons (str) in case it's not compliant. Reasons can
        be "word_count", "first_letter" or "punctuation".
    """
    # Rules:
    # - At least 5 words (word_count)
    # - First letter must be capitalized (first_letter)
    # - Trailing punctuation (punctuation)
    compliance = {
        "word_count": True,
        "first_letter": True,
        "punctuation": True,
    }
    # This needs to be done better... Needs a more comprehensive database.
    punctuational_textual_emotes_and_symbols: list[str] = [
        "-",
        "xD",
        "x.x",
        ":c",
        "o7",
        ":3",
        "q.q",
        ":0",
    ]

    if word_count_ < 5:
        compliance["word_count"] = False
    try:
        first_letter_index = _find_first_letter_index(content)
        if first_letter_index is None:  # No letter in msg
            compliance["first_letter"] = False
        elif not content[first_letter_index].isupper():
            compliance["first_letter"] = False
        if content[-1] not in string.punctuation:
            for i in punctuational_textual_emotes_and_symbols:
                if content.endswith(i):
                    break
            else:
                compliance["punctuation"] = False
    except IndexError:
        # Content is empty. Example: https://uwmc.de/p108813
        compliance["first_letter"] = False
        compliance["punctuation"] = False

    broken_rules: list[Optional[str]] = [
        key for key, value in compliance.items() if not value
    ]
    return (not any(broken_rules), broken_rules)


def get_message_creation_time(message: bs4.element.Tag) -> dt.datetime:
    """Retrieves a messages creation date.

    Args:
        message (bs4.element.Tag): The message's element tag.

    Returns:
        datetime.datetime: A datetime.datetime object representing the
        message's creation date.
    """
    iso_string = message.find_all("time", class_="u-dt")[0]["datetime"]
    return dateparser.parse(iso_string)  # type: ignore


def _find_first_letter_index(string_: str):
    for char in string_:
        if char in string.ascii_letters:
            return string_.index(char)
    return None


# Source: https://de.m.wikipedia.org/wiki/Satzzeichen
PUNCTUATION = r".?!\"„“‚‘»«‹›,;:'’–—‐\-·/\()\[\]<>{}…☞‽¡¿⸘、"


def rules_reworked(content: str) -> dict[str, bool]:
    content = content.strip()  # Remove trailing and leading whitespaces
    content = content.strip(WHITESPACES)  # Better list

    compliance = {
        "word_count": True,
        "first_letter": True,
        "punctuation": True,
    }

    if not content:
        return {
            k: False for k in compliance.keys()
        }

    # Word count
    # Using re.split() instead of str.split() as re supports all whitespaces
    # out of the box.
    word_count = len(re.split(
        fr"[\s{PUNCTUATION}]",  # Split words by whitespace and punctuation
        content,
    ))
    if word_count < 5:
        compliance["word_count"] = False

    # First Letter
    # Get index of first letter
    # This requires the third party regex module to work
    # Therefore `import regex as re`
    # This works with all Unicode letters, including äöü, ß and âáà.
    first_letter_match = re.search('\\p{L}', content, re.UNICODE)
    if (
        first_letter_match is None  # No letter in content
        or not first_letter_match.captures()[0].isupper()  # noqa  # letter not uppercase
    ):
        compliance["first_letter"] = False

    # Punctuation
    # Last forum emoji has already been replaced with a dot
    last_char = content[-1]
    if (
        last_char not in PUNCTUATION
        and not is_emoji(last_char)  # noqa
    ):
        compliance["punctuation"] = False

    return compliance
