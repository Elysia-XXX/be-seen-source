"""Conservative comparison of supplied address components, without geocoding.

An omitted component is unknown, never evidence of a conflicting location.
Only structured address/city fields supported by citations belong here.
"""
import re
import unicodedata


def location_text(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(str.maketrans("廣區縣號門東陽萬臺鎮鄉樓層", "广区县号门东阳万台镇乡楼层"))
    return re.sub(r"[\s,，。:：]+", "", value)


def parts(value):
    remaining = location_text(value)
    result = {"province": "", "city": "", "district": "", "detail": ""}
    # Parse only the administrative prefix, never arbitrary prose or map labels.
    for level, suffix in (("province", "省"), ("city", "市"), ("district", "区|县")):
        match = re.match(rf"^([^省市区县路街道号镇乡()（）]{{1,12}}?(?:{suffix}))", remaining)
        if match:
            result[level] = match[0]
            remaining = remaining[len(match[0]):]
    result["detail"] = remaining
    return result


def profile_parts(profile):
    result = parts(profile.city)
    # City may be supplied without 市. Do not treat it as a street address.
    if not result["city"] and result["detail"]:
        result["city"] = result["detail"].removesuffix("市") + "市"
    result["detail"] = ""
    for value in (profile.district, profile.address):
        parsed = parts(value)
        for level in ("province", "city", "district"):
            if parsed[level]:
                result[level] = parsed[level]
    result["detail"] = parts(profile.address)["detail"]
    # Also accept omission of administrative suffixes when explicitly supplied.
    for value in (profile.city, profile.district):
        token = location_text(value)
        if token and result["detail"].startswith(token):
            result["detail"] = result["detail"][len(token):]
    return result


def has_store_address(profile):
    return bool(profile_parts(profile)["detail"])


def location_relation(profile, address="", city=""):
    expected = profile_parts(profile)
    observed = parts(address)
    stated_city = parts(city)
    if not stated_city["city"] and stated_city["detail"]:
        stated_city["city"] = stated_city["detail"].removesuffix("市") + "市"
    for level in ("province", "city", "district"):
        actual = observed[level] or stated_city[level]
        if expected[level] and actual and expected[level] != actual:
            return level + "_conflict"
    target, actual = expected["detail"], observed["detail"]
    if target:
        if not actual:
            return "unknown"
        if target == actual:
            return "compatible"
        # Only compare complete road/number components. A missing number does
        # not prove another branch, and 1号 must not match 11号 or 1号楼.
        street = r"^(.+?(?:大道|大街|公路|路|街|巷|弄))([0-9一二三四五六七八九十百零〇-]+号)?"
        a, b = re.match(street, target), re.match(street, actual)
        if a and b:
            if a[1] != b[1] or (a[2] and b[2] and a[2] != b[2]):
                return "address_conflict"
        return "unknown"
    for level in ("district", "city", "province"):
        if expected[level]:
            return "compatible" if expected[level] == (observed[level] or stated_city[level]) else "unknown"
    return "unknown"


def correct_location_suggestion(item, profile):
    """Never promote an uncertain identity just because locations overlap."""
    if item["field"] not in ("district", "address") or item["status"] != "conflict":
        return item
    relation = location_relation(profile, item["value"])
    if relation.endswith("_conflict"):
        return item
    reason = ("来源位置与已填区域兼容；更详细的地址不构成冲突，是否属于目标商家仍待确认。"
              if relation == "compatible" else "现有位置证据不足以认定冲突，商家身份及具体门店仍待确认。")
    return {**item, "status": "uncertain", "reason": reason}
