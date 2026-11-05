"""Team names: one canonical name per D1 team (KenPom/Torvik spelling), plus every alias seen from other sites."""
import json, os, re, unicodedata

TEAMS = json.load(open(os.path.join(os.path.dirname(__file__), "teams.json")))   # name -> conference

ALIAS = {'Cal State Bakersfield': 'Cal St. Bakersfield', 'CSU Bakersfield': 'Cal St. Bakersfield', 'Abil. Christian': 'Abilene Christian',
 'American University': 'American',
 'App State': 'Appalachian St.',
 'App State Mountaineers': 'Appalachian St.',
 'Appalachian State': 'Appalachian St.',
 'Bethune-Cookman': 'Bethune Cookman',
 'Boston U': 'Boston University',
 'Cal State Northridge Matadors': 'CSUN',
 'Cent. Arkansas': 'Central Arkansas',
 'Cent. Conn. St.': 'Central Connecticut',
 'Cent. Michigan': 'Central Michigan',
 'Charleston So.': 'Charleston Southern',
 'Coast. Carolina': 'Coastal Carolina',
 'Detroit': 'Detroit Mercy',
 'E. Illinois': 'Eastern Illinois',
 'E. Michigan': 'Eastern Michigan',
 'E. Washington': 'Eastern Washington',
 'ECU': 'East Carolina',
 'ETSU': 'East Tennessee St.',
 'FAU': 'Florida Atlantic',
 'FGCU': 'Florida Gulf Coast',
 'Fair. Dickinson': 'Fairleigh Dickinson',
 'Florida International': 'FIU',
 'G. Washington': 'George Washington',
 'GA Southern': 'Georgia Southern',
 'Gardner-Webb': 'Gardner Webb',
 'Grambling': 'Grambling St.',
 'Grambling Tigers': 'Grambling St.',
 "Hawai'i": 'Hawaii',
 'IU Indianapolis': 'IU Indy',
 'IU Indianapolis Jaguars': 'IU Indy',
 'Illinois State': 'Illinois St.',
 'JMU': 'James Madison',
 'LA Tech': 'Louisiana Tech',
 'Long Island': 'LIU',
 'Long Island University': 'LIU',
 'Long Island University Sharks': 'LIU',
 'Loyola (MD)': 'Loyola MD',
 'Loyola Maryland': 'Loyola MD',
 'Loyola Maryland Greyhounds': 'Loyola MD',
 'Loyola-Chicago': 'Loyola Chicago',
 'McNeese St.': 'McNeese',
 'Miami': 'Miami FL',
 'Miami (Fla.)': 'Miami FL',
 'Miami (OH)': 'Miami OH',
 'Miami Hurricanes': 'Miami FL',
 "Mt. St. Mary's": "Mount St. Mary's",
 'N. Illinois': 'Northern Illinois',
 'N. Kentucky': 'Northern Kentucky',
 'NC Central': 'North Carolina Central',
 'NC State': 'N.C. State',
 'Ole Miss': 'Mississippi',
 'Ole Miss Rebels': 'Mississippi',
 'Omaha': 'Nebraska Omaha',
 'Omaha Mavericks': 'Nebraska Omaha',
 'Pennsylvania': 'Penn',
 'Queens University': 'Queens',
 'S. Indiana': 'Southern Indiana',
 'SE Louisiana': 'Southeastern Louisiana',
 'SE Louisiana Lions': 'Southeastern Louisiana',
 'SE Missouri St.': 'Southeast Missouri',
 'SIU Edwardsville': 'SIUE',
 "Saint Joe's": "Saint Joseph's",
 'Sam Houston': 'Sam Houston St.',
 'Sam Houston Bearkats': 'Sam Houston St.',
 'San Jose State': 'San Jose St.',
 'Seattle U': 'Seattle',
 'South Carolina Upstate': 'USC Upstate',
 'Southeast Missouri St.': 'Southeast Missouri',
 'Southeast Missouri State': 'Southeast Missouri',
 "St. Peter's": "Saint Peter's",
 'St. Thomas (MN)': 'St. Thomas',
 'St. Thomas-Minnesota': 'St. Thomas',
 'Tarleton State': 'Tarleton St.',
 'Tenn. Tech': 'Tennessee Tech',
 'Texas A&M-CC': 'Texas A&M Corpus Chris',
 'Texas A&M-Corpus Chr...': 'Texas A&M Corpus Chris',
 'Texas A&M-Corpus Christi': 'Texas A&M Corpus Chris',
 'UAlbany': 'Albany',
 'UAlbany Great Danes': 'Albany',
 'UConn': 'Connecticut',
 'UConn Huskies': 'Connecticut',
 'UIC': 'Illinois Chicago',
 'UIC Flames': 'Illinois Chicago',
 'UL Monroe': 'Louisiana Monroe',
 'UL Monroe Warhawks': 'Louisiana Monroe',
 'UMES': 'Maryland Eastern Shore',
 'UMKC': 'Kansas City',
 'UMass': 'Massachusetts',
 'UNH': 'New Hampshire',
 'USF': 'South Florida',
 'UT Martin': 'Tennessee Martin',
 'UT Martin Skyhawks': 'Tennessee Martin',
 'W. Carolina': 'Western Carolina',
 'W. Illinois': 'Western Illinois',
 'W. Kentucky': 'Western Kentucky',
 'W. Michigan': 'Western Michigan',
 'Wofford Terriers': 'Wofford'}


# Our canonical names follow KenPom; T-Rank (and cbbdata) differ for a handful of teams.
TORVIK = {"McNeese": "McNeese St.", "Kansas City": "UMKC", "Southeast Missouri": "Southeast Missouri St.",
          "Texas A&M Corpus Chris": "Texas A&M Corpus Chris", "Nicholls": "Nicholls St.", "SIUE": "SIU Edwardsville"}


def deaccent(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s):
    s = deaccent(s).replace("State", "St.").replace("&amp;", "&")
    return re.sub(r"[^a-z0-9&]", "", s.lower())


NORMIDX = {norm(t): t for t in TEAMS}
# DRatings writes full names with mascots; these are the ones whose first words name a different school
ALIAS.update({"Pitt": "Pittsburgh", "Wisconsin Green Bay": "Green Bay", "Wisconsin Milwaukee": "Milwaukee",
              "Texas A&M Commerce": "East Texas A&M", "Texas A&M Corpus Christi": "Texas A&M Corpus Chris",
              "Arkansas Little Rock": "Little Rock", "Arkansas Pine Bluff": "Arkansas Pine Bluff", "Tennessee Martin": "UT Martin",
              "Louisiana Monroe": "Louisiana Monroe", "Texas Rio Grande Valley": "UT Rio Grande Valley", "Missouri Kansas City": "Kansas City",
              "Indiana Purdue Indianapolis": "IU Indy", "Illinois Chicago": "Illinois Chicago", "Maryland Baltimore County": "UMBC",
              "Massachusetts Lowell": "UMass Lowell", "North Carolina Wilmington": "UNC Wilmington", "North Carolina Greensboro": "UNC Greensboro",
              "North Carolina Asheville": "UNC Asheville", "Texas San Antonio": "UTSA", "Texas Arlington": "UT Arlington", "Texas El Paso": "UTEP",
              "Southern Illinois Edwardsville": "SIU Edwardsville", "Nebraska Omaha": "Nebraska Omaha", "Mississippi Valley State": "Mississippi Valley St."})
ALIAS = {k: v for k, v in ALIAS.items() if v in TEAMS}
PREFIXIDX = dict(NORMIDX)
PREFIXIDX.update({norm(k): v for k, v in ALIAS.items()})


def to_canon(name, allow_prefix=False):
    """Exact name, known alias, or normalized match; with allow_prefix, the longest team whose name starts the text
    (ESPN's "Iowa State Cyclones")."""
    if not name:
        return None
    name = deaccent(re.sub(r"^\d{1,2}\s+", "", name.strip()))
    if name in TEAMS:
        return name
    if name in ALIAS:
        return ALIAS[name]
    n = norm(name)
    if n in NORMIDX:
        return NORMIDX[n]
    if allow_prefix:
        best = None
        for k, t in PREFIXIDX.items():
            if n.startswith(k) and (best is None or len(k) > len(best[0])):
                best = (k, t)
        if best:
            return best[1]
    return None


MASSEY = {"CS Bakersfield": "Cal St. Bakersfield", "Central Conn": "Central Connecticut", "Charleston So": "Charleston Southern",
          "Coastal Car": "Coastal Carolina", "LIU Brooklyn": "LIU", "MA Lowell": "UMass Lowell", "McNeese St": "McNeese",
          "Northwestern LA": "Northwestern St.", "Prairie View": "Prairie View A&M", "SUNY Albany": "Albany",
          "St Peter's": "Saint Peter's", "WI Green Bay": "Green Bay", "WI Milwaukee": "Milwaukee", "WKU": "Western Kentucky",
          "Pitt": "Pittsburgh", "Pitt Panthers": "Pittsburgh", "TAM C. Christi": "Texas A&M Corpus Chris", "TX Southern": "Texas Southern",
          "UT San Antonio": "UTSA", "UTRGV": "UT Rio Grande Valley", "Ark Pine Bluff": "Arkansas Pine Bluff", "Ark Little Rock": "Little Rock",
          "FL Gulf Coast": "Florida Gulf Coast", "Florida Intl": "FIU", "Loyola MD": "Loyola MD", "Loyola-Chicago": "Loyola Chicago",
          "Miami OH": "Miami OH", "Miami FL": "Miami FL", "MS Valley St": "Mississippi Valley St.", "MD E Shore": "Maryland Eastern Shore",
          "NC A&T": "North Carolina A&T", "NC Central": "North Carolina Central", "S Carolina St": "South Carolina St.",
          "SE Missouri St": "Southeast Missouri", "SE Louisiana": "Southeastern Louisiana", "SF Austin": "Stephen F. Austin",
          "St Mary's CA": "Saint Mary's", "St Joseph's PA": "Saint Joseph's", "St Louis": "Saint Louis",
          "Mt St Mary's": "Mount St. Mary's", "Houston Chr": "Houston Christian", "Incarnate Word": "Incarnate Word",
          "IL Chicago": "Illinois Chicago", "IUPUI": "IU Indy", "Kennesaw": "Kennesaw St.", "UNC Wilmington": "UNC Wilmington",
          "Citadel": "The Citadel", "ETSU": "East Tennessee St.", "UC Santa Barbara": "UC Santa Barbara", "Cal Baptist": "Cal Baptist",
          "CS Fullerton": "Cal St. Fullerton", "CS Northridge": "CSUN", "Sacramento St": "Sacramento St.", "Long Beach St": "Long Beach St.",
          "Southern Univ": "Southern", "Grambling": "Grambling St.", "Texas A&M Corpus Christi": "Texas A&M Corpus Chris"}
_DIR = [("N ", ("Northern ", "North ")), ("S ", ("Southern ", "South ")), ("E ", ("Eastern ", "East ")), ("W ", ("Western ", "West ")),
        ("C ", ("Central ",)), ("SE ", ("Southeast ", "Southeastern ")), ("NW ", ("Northwestern ",)), ("SW ", ("Southwestern ",))]


def to_canon_abbr(name):
    """Massey-style abbreviations ("W Michigan", "Central Conn", "St Peter's"): exact/alias match, then the MASSEY table,
    then directional prefixes spelled out. No prefix guessing, so a non-D1 name ("Howard Payne") stays unmatched."""
    name = (name or "").strip()
    c = to_canon(name) or (MASSEY.get(name) and to_canon(MASSEY[name]))
    if c:
        return c
    for ab, full in _DIR:
        if name.startswith(ab):
            for f in full:
                c = to_canon(f + name[len(ab):])
                if c:
                    return c
    if name.startswith("St "):
        return to_canon("Saint " + name[3:]) or to_canon("St. " + name[3:])
    return None


def canon_tail(text, max_words=6):
    """The longest run of trailing words in text that names a team ("12:00 PM 14 Florida" -> "Florida")."""
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    for k in range(min(max_words, len(words)), 0, -1):
        c = to_canon(" ".join(words[-k:]))
        if c:
            return c
    return None
