from enum import Enum
import re
import sys

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By 
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tabulate import tabulate


_FACTIONS = [
    'ambas', 'baltaks', 'bescods', 'firaks', 'geodens', 'gleens', 'hadsch-hallas', 
    'itars', 'ivits', 'lantids', 'nevlas', 'taklons', 'terrans', 'xenos']

_TECH_TRACKS = ['terra', 'nav', 'int', 'gaia', 'eco', 'sci']


class Res(Enum):
    COIN = 1
    ORE = 2
    KNOWLEDGE = 3
    QIC = 4
    POWER = 5
    PT = 6
    VP = 7


class ChangeType(Enum):
    GAIN = 1
    LOSS = 2


class StateChange:
    """Represents a single change in the game state."""
    
    _RESOURCE_MAP = {
        'c':  Res.COIN,
        'o':  Res.ORE,
        'k':  Res.KNOWLEDGE,
        'q':  Res.QIC,
        'pw': Res.POWER,
        't':  Res.PT,
        'vp': Res.VP
    }
    
    def __init__(self, state_change_string):
        if not state_change_string:
            raise ValueError('state_change_string cannot be empty.')
            
        # Determine whether this state change marks the loss or gain of resources.
        if state_change_string[0] == '-':
            self.type = ChangeType.LOSS
        else:
            self.type = ChangeType.GAIN
        
        # Determine which resource the state change refers to.
        for res_string, res_type in StateChange._RESOURCE_MAP.items():
            if state_change_string.endswith(res_string):
                self.resource = res_type
                break 
        
        # Determine the quantity of resource gained or lost.
        try:
            self.quantity = int(re.findall(r'\d+', state_change_string)[0])
        except IndexError:
            print('No quantity found in ' + state_change_string)
            sys.exit(1)
    
    def __repr__(self):
        return 'StateChange(type={}, resource={}, quantity={})'.format(
            self.type, self.resource, self.quantity)


class LogItem:
    """A single log item."""

    def __init__(self, text, faction, events):
        """
        Constructs a log item.

        :param text: Text describing what was logged.
        :param faction: The faction (if any) whose action created the log item.
        :param events: A list of state-modifying events that occurred during the primary action.
        """
        self.text = text
        self.faction = faction
        self.events = events

    def __repr__(self):
        return "LogItem(text='{}', faction={}, events={})".format(
            self.text, self.faction, self.events)

    @staticmethod 
    def _get_faction(text):
        """Check whether an action had an associated faction."""
        for faction in _FACTIONS:
            if faction in text:
                return faction
        return None

    @staticmethod
    def _compute_events(actions_html, state_change_html):
        """Compute the events that occurred during each action."""
        actions = [act.string for act in actions_html.find_all('div')]
        state_changes = [st.string for st in state_change_html.find_all('div')]
        events = []
        for action, state_change in zip(actions, state_changes):
            action = action.strip()
            state_change_list = state_change.strip().replace(',', '').split(' ')
            change_list = []
            for change in state_change_list:
                if change.strip():
                    change_list.append(StateChange(change.strip()))
                else:
                    change_list.append(None)
            events.append((action, change_list))
        return events

    @staticmethod
    def parse_from_HTML(row):
        """Constructs a LogItem from a <tr> HTML element."""
        cols = row.find_all('td')
        if len(cols) < 1:
            raise ValueError('The row {} is empty.'.format(row))
        # We check for a faction regardless of the number of columns in the row.
        text = cols[0].string.strip()
        faction = LogItem._get_faction(text)
        events = None
        if len(cols) == 3:
            # There are three columns in the row, so this action did have an effect on the game state.
            events = LogItem._compute_events(cols[1], cols[2])
        return LogItem(text, faction, events)


class GameLog:
    """A log of all actions taken in a game."""

    def __init__(self, factions, items):
        """Constructs a game log object."""
        self.factions = factions
        self.items = items

    @staticmethod
    def parse_from_HTML(html):
        """Parses a game log from an HTML element."""
        log_rows = html.find_all('tr', html.table.tbody)
        # Reverse the rows in the log so that the actions are in the correct sequence.
        log_rows.reverse()
        items = [LogItem.parse_from_HTML(row) for row in log_rows]
        factions = set()
        for item in items:
            if item.faction:
                stripped_faction = item.faction.strip()
                if stripped_faction not in factions:
                    factions.add(stripped_faction)
        return GameLog(factions, items)


class VPStats:
    """Object which tracks all stats related to VP counts."""

    def __init__(self):
        self.vp = 10
        self.vp_lost_from_leech = 0
        self.vp_from_round_scoring = 0
        self.vp_from_boosters = 0
        self.vp_from_endgame = 0
        self.vp_from_techs = 0
        self.vp_from_adv_techs = 0
        self.vp_from_feds = 0
        self.vp_from_qic_act = 0
        self.vp_from_tracks = 0
        self.vp_from_resources = 0

    def update_vp(self, action, change):
        """Increment VP statistics according to the action performed."""
        # change.type must be either ChangeType.GAIN or ChangeType.LOSS
        assert change.type is ChangeType.GAIN or change.type is ChangeType.LOSS
        # If this doesn't change VP count, ignore it.
        if not change.resource == Res.VP:
            return
        elif 'round' in action:
            self.vp_from_round_scoring += change.quantity
        elif 'booster' in action:
            self.vp_from_boosters += change.quantity
        elif 'final' in action:
            self.vp_from_endgame += change.quantity
        elif 'tech' in action:
            self.vp_from_techs += change.quantity
        elif 'adv' in action:
            self.vp_from_adv_techs += change.quantity
        elif 'federation' == action:
            self.vp_from_feds += change.quantity
        elif 'qic' in action:
            self.vp_from_qic_act += change.quantity
        elif action in _TECH_TRACKS:
            self.vp_from_tracks += change.quantity
        elif 'spend' == action:
            self.vp_from_resources += change.quantity
        elif 'charge' == action:
            self.vp_lost_from_leech -= change.quantity
        # Increment or decrement total VP stats accordingly.
        if change.type is ChangeType.GAIN:
            self.vp += change.quantity
        elif change.type is ChangeType.LOSS:
            self.vp -= change.quantity


class ResourceStats:
    """Object which tracks all stats related to non-VP resources."""
    
    _RESOURCE_TO_FIELD_MAP = {
        Res.POWER: 'power',
        Res.COIN: 'coins',
        Res.ORE: 'ore',
        Res.KNOWLEDGE: 'knowledge',
        Res.QIC: 'qic',
        Res.PT: 'pt',
    }

    def __init__(self):
        self.leech = 0
        self.power = 0
        self.coins = 0
        self.ore = 0
        self.knowledge = 0
        self.qic = 0
        self.pt = 0

    def update_resources(self, action, change):
        """Increment resource statistics according to action performed."""
        # Currently we only track total number of resources gained.
        if change.type is ChangeType.LOSS:
            return
        # The type of change should be ChangeType.GAIN if it is not ChangeType.LOSS.
        assert change.type == ChangeType.GAIN
        # Handle leech specially as it depends on the action taken, not the resource.
        if 'charge' == action:
            self.leech += change.quantity
            
        field = ResourceStats._RESOURCE_TO_FIELD_MAP[change.resource]
        current_value = getattr(self,field)
        setattr(self, field, current_value + change.quantity)


class FactionStats(VPStats, ResourceStats):
    """Track statistics for a specific faction in a game."""

    def __init__(self, faction):
        self.faction = faction
        VPStats.__init__(self)
        ResourceStats.__init__(self)

    def augment(self, event):
        """Augment faction stats with data from a new event."""
        action = event[0]
        changes = event[1]
        for change in changes:
            if change.resource == Res.VP:
                self.update_vp(action, change)
            else:
                self.update_resources(action, change)


class Stats:
    """Compute statistics from a given GameLog."""

    def __init__(self, log):
        self.log = log
        self.faction_stats = {faction: FactionStats(faction) for faction in self.log.factions}
        for item in self.log.items:
            if item.faction and item.events:
                for event in item.events:
                    self.faction_stats[item.faction].augment(event)
    
    def breakdown_vp(self):
        """Perform a breakdown of the VP gained by each faction."""
        # First print a breakdown of the number of points gained from each category.
        print('VP breakdown:')
        headers = ['Faction', 'Total VP', 'Round', 'Boosters', 'Endgame', 'Techs', 'Adv. Techs', 'Feds', 'QIC Actions', 'Tracks', 'Resources', 'Leech']
        rows = []
        for faction, stats in self.faction_stats.items():
            rows.append([
                faction,
                stats.vp,
                stats.vp_from_round_scoring,
                stats.vp_from_boosters,
                stats.vp_from_endgame,
                stats.vp_from_techs,
                stats.vp_from_adv_techs,
                stats.vp_from_feds,
                stats.vp_from_qic_act,
                stats.vp_from_tracks,
                stats.vp_from_resources,
                stats.vp_lost_from_leech,
            ])
        print(tabulate(rows, headers=headers))
        print()
        # Next, print a breakdown of what percentage of the total VP each category contributed.
        print('VP Percentages:')
        headers.remove('Total VP')
        rows = []
        for faction, stats in self.faction_stats.items():
            rows.append([
                faction,
                stats.vp_from_round_scoring/stats.vp*100,
                stats.vp_from_boosters/stats.vp*100,
                stats.vp_from_endgame/stats.vp*100,
                stats.vp_from_techs/stats.vp*100,
                stats.vp_from_adv_techs/stats.vp*100,
                stats.vp_from_feds/stats.vp*100,
                stats.vp_from_qic_act/stats.vp*100,
                stats.vp_from_tracks/stats.vp*100,
                stats.vp_from_resources/stats.vp*100,
                stats.vp_lost_from_leech/stats.vp*100,
            ])
        print(tabulate(rows, headers=headers, floatfmt='.2f'))
        print()

    def breakdown_resources(self):
        """Performs a breakdown of the resources gained by each faction."""
        print('Resources breakdown:')
        headers = ['Faction', 'Power', 'Leech', 'Coins', 'Ore', 'Knowledge', 'QIC', 'Power Tokens']
        rows = []
        for faction, stats in self.faction_stats.items():
            rows.append([
                faction,
                stats.power,
                stats.leech,
                stats.coins,
                stats.ore,
                stats.knowledge,
                stats.qic,
                stats.pt,
            ])
        print(tabulate(rows, headers=headers))

    def breakdown(self):
        """Breakdown VP and resources."""
        self.breakdown_vp()
        self.breakdown_resources()


def test_main():
    """Use a local HTML file to test stats breakdown."""
    html = None
    with open('/home/jgunter/Projects/gaia-project-scraper/test_log.txt') as f:
        html = f.read()

    if html:
        soup = BeautifulSoup(html, 'lxml')
        raw_game_log = soup.find('div', class_='col-12 order-last mt-4')
        log = GameLog.parse_from_HTML(raw_game_log)
        stats = Stats(log)
        stats.breakdown()


def main():
    if len(sys.argv) < 2:
        print('A game URL must be supplied.')
        sys.exit(1)

    html = None
    url = sys.argv[1]
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    browser = webdriver.Chrome(options=chrome_options)
    delay = 10
    browser.get(url)

    try:
        WebDriverWait(browser, delay).until(
            EC.presence_of_element_located((By.ID, 'game-iframe'))
        )
        browser.switch_to.frame('game-iframe')
    except TimeoutException:
        print('Loading took too much time!')
    else:
        html = browser.page_source
    
    if html:
        soup = BeautifulSoup(html, 'lxml')
        raw_game_log = soup.find('div', class_='col-12 order-last mt-4')
        log = GameLog.parse_from_HTML(raw_game_log)
        stats = Stats(log)
        stats.breakdown()


if __name__=='__main__':
    main()
