"""A partial or mismatched website result must never certify a generated list."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from edhrec_affinity.build_styles.external_validation import record_external_validation, external_restriction_evidence
from edhrec_affinity.build_styles.official_checker import OfficialDeckChecker, deck_url, parse_observation


def deck():
    return {'card_count':100, 'commanders':[{'name':'Nemata, Grove Guardian'}], 'cards':[
        {'name':'Nemata, Grove Guardian', 'quantity':1}, {'name':'Forest', 'quantity':96},
        {'name':'Vorinclex, Voice of Hunger', 'quantity':1}, {'name':'Time Warp', 'quantity':1},
        {'name':'Seedborn Muse', 'quantity':1}]}


def snapshot():
    return {'state':{'cards':[{**c, 'matched':True, 'flavor_name':[]} for c in deck()['cards']],
                     'commanders':['Nemata, Grove Guardian'], 'loading':'hidden', 'toolError':False,
                     'combosComplete':True},
            'groups':[
                {'heading':'Extra Turns: 1', 'items':[{'text':'Time Warp'}]},
                {'heading':'Mass Land Denial: 1', 'items':[{'text':'Vorinclex, Voice of Hunger'}]},
                {'heading':'Early 2-Card Combos: 0 ', 'items':[]},
                {'heading':'Late 2-Card Combos: 0 ', 'items':[]},
                {'heading':'Game Changers: 1', 'items':[{'text':'Seedborn Muse'}]}],
            'chaining':['Time Warp'], 'details':'Recommended Bracket: 4Minimum Bracket: 4',
            'power':'6.71', 'playability':'75.3%', 'imported':'100'}


def parse(s=None, d=None):
    d=d or deck()
    return parse_observation(d,s or snapshot(),source_url=deck_url(d),script_sha256='a'*64,
                             checked_at=datetime.now(timezone.utc).isoformat())


def test_actual_complete_result_roundtrips_with_role_bound_proof(tmp_path):
    rating=record_external_validation(deck(),parse(),tmp_path)
    assert rating['score_bracket']==3
    evidence=external_restriction_evidence(rating)
    assert evidence['mass_land_denial']==['Vorinclex, Voice of Hunger']
    assert evidence['chaining_extra_turns']==['Time Warp']
    assert evidence['early_combos']==[]


@pytest.mark.parametrize('field,value', [('loading','visible'),('toolError',True),('combosComplete',False),
    ('commanders',['Seedborn Muse'])])
def test_incomplete_state_or_wrong_roles_rejected(field,value):
    s=snapshot();s['state'][field]=value
    with pytest.raises(ValueError):parse(s)


@pytest.mark.parametrize('change', ['missing_group','count_mismatch','combo_error','unresolved','quantity',
    'foreign_card','foreign_restriction','missing_chaining','inconsistent_chaining','missing_score','wrong_import'])
def test_partial_or_wrong_assessments_fail_closed(change):
    s=snapshot()
    if change=='missing_group':s['groups'].pop()
    elif change=='count_mismatch':s['groups'][0]['heading']='Extra Turns: 2'
    elif change=='combo_error':s['groups'][2]['heading']+='(combo data retrieval error)'
    elif change=='unresolved':s['state']['cards'][0]['matched']=False
    elif change=='quantity':s['state']['cards'][1]['quantity']=95
    elif change=='foreign_card':s['state']['cards'][0]['name']='Other Commander'
    elif change=='foreign_restriction':s['groups'][1]['items'][0]['text']='Armageddon'
    elif change=='missing_chaining':s['chaining']=None
    elif change=='inconsistent_chaining':s['chaining']=['Forest']
    elif change=='missing_score':s['power']=None
    elif change=='wrong_import':s['imported']='99'
    with pytest.raises(ValueError):parse(s)


def test_site_flavor_alias_array_and_combo_categories_are_preserved():
    s=snapshot()
    s['state']['cards'][2].update(name='Oracle Card Name',flavor_name=['Vorinclex, Voice of Hunger'])
    s['groups'][1]['items'][0]['text']='Oracle Card Name'
    s['groups'][3]={'heading':'Late 2-Card Combos: 1', 'items':[
        {'text':'Oracle Card Name + Seedborn Muse','href':'https://commanderspellbook.com/combo/1-2'}]}
    values=parse(s)
    assert values['bracket_restrictions']['late_combos']==[['Vorinclex, Voice of Hunger','Seedborn Muse']]


def test_mld_duplicate_raw_matches_deduplicated_after_count_validation():
    s=snapshot();s['groups'][1]['heading']='Mass Land Denial: 2'
    s['groups'][1]['items']*=2
    assert parse(s)['bracket_restrictions']['mass_land_denial']==['Vorinclex, Voice of Hunger']


def test_preload_preserves_command_zone_and_slash_spacing():
    d=deck();d['cards'][2]['name']='Front // Back'
    payload=parse_qs(urlparse(deck_url(d)).query)['d'][0]
    assert '1 Front // Back' in payload
    assert payload.endswith('~~1 Nemata, Grove Guardian~Z~')


@pytest.mark.parametrize('status',[401,403,429])
def test_source_block_survives_navigation_error_and_stops_next_deck(status,tmp_path):
    class Page:
        callback=None
        closed=False
        def on(self,event,callback):self.callback=callback
        def goto(self,*args,**kwargs):
            response=type('Response',(),{'url':'https://getcards-4zjvieuafa-uc.a.run.app','status':status,
                'headers':{'retry-after':'172800'}})()
            self.callback(response)
            raise RuntimeError('Navigation interrupted')
        def close(self):self.closed=True
    page=Page()
    class Context:
        calls=0
        def new_page(self):self.calls+=1;return page
    checker=OfficialDeckChecker();checker._context=Context()
    first=checker.check(deck(),validation_directory=tmp_path)
    second=checker.check(deck(),validation_directory=tmp_path)
    assert first['status']==second['status']=='blocked'
    assert first['source_blocked'] and str(status) in first['reason']
    assert first['retry_after_seconds']==second['retry_after_seconds']==172800
    assert checker._context.calls==1 and page.closed
    assert not list(tmp_path.iterdir())


def test_cleanup_after_failed_browser_startup_is_safe_and_idempotent():
    checker=OfficialDeckChecker()
    checker.__exit__(None,None,None)
    checker.__exit__(None,None,None)
