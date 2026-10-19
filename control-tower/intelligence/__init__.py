"""
ATLAS Intelligence — the learning layer of the ATA Control Tower.

    OBSERVE   events.py     what happened, run after run, with provenance
    LEARN     learning.py   what it adds up to — verified outcomes only
    PLAN      plans.py      recovery plans from the safe-action policy and
                            what verifiably worked before
    EVIDENCE  evidence.py   real screenshots and page text, indexed;
              vision.py     reading an image, facts kept apart from inference
    EVALUATE  maturity.py   the monthly evaluation and the star level

Standard library only. Nothing here touches a browser, a page or the Hub,
and nothing here changes how the automation behaves: it observes, learns and
proposes. A proposal changes production only through a tested, approved
deployment — never by itself.
"""
