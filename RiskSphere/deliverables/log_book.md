# log book

rough notes. not a report. just how this thing actually got built.

- started from the case writeup. six names, limits taken off a simulated peak, python does the money. the model was not supposed to invent a margin number. that rule kept getting broken and we kept putting it back.

- first cut was one agent that planned and also sounded like it had done the maths. we split it. orchestrator calls qwen for the plan and the sentences. documents searches. later we let documents call qwen once, only to copy one clause. formulas stayed in python. for a while the writeup still said there were more agents than we had.

- filings took longer than the code. edgar search. the csa folder landed at 522 because annexes from the contract crawl got saved in there. contracts stuck at 278, we had wanted 300, we left the hole rather than pad it. the bis urls returned html. the 2020 bcbs pdf is the one that actually downloaded.

- prices are live yahoo. when a download failed we stopped. we did not paste yesterday's close and call it today. some last closes were a day behind. we print the date next to the price.

- qwen. temperature 0, /no_think, english only, 400 tokens. it still came back with chinese or with json that was not a plan. the keyword planner is only for when the gateway fails. we do not put that path in the score.

- prompt tuning was mostly stop deciding. early on the plan was allowed to send a threshold. it made one up. now the plan can only ask for a search. python reads the passage. the quote has to be in the file, one amount, or it does not become a draft. the model cannot sign. a person types a name.

- the critic drops sentences that quote a number it cannot find in the tool json. it also drops true ones. 1,000,000 gets split so it looks like 000. the eurusd close 1.096972 left 096972 looking invented. we left the check strict. the python line is the answer when the sentence is thrown out.

- retrieval is 24 sample sentences, not the filing folder. bm25 plus the small embedder. "what does the schedule say about gold" works. "bullion" and "shares" never made the top 5. gold and equity are the words in the passages. we did not pretend the model understood the paraphrase.

- then the questions where the passage is there and the answer is not. threshold is 1,000,000, exposure below it needs no delivery, the question says 800,000. retrieval finds the sentence and cannot compare. we added the comparison in python. same for rounding up 14,000 to 20,000. if the sentence does not grant the thing, we say it does not. close-out, an exemption, lending collateral overnight, swapping treasuries for corporate bonds. no.

- gateway is one request at a time, 60 a minute. a 429 or a 503 waits 2 seconds, then 4, then 8. the key sits in a local file. we do not print it and we do not commit it.

- other things we hit and did not tidy away. meridian's factor is under 1 so that name shows a breach on purpose. kaveri's simulated peak came out 0, that is what the paths did. harbor lane is in the sample clauses and is not one of the six names. the trace file only appends, so the dashboard looks huge. it is old runs. the frozen 27 stayed frozen. the 110 wordings are a separate list. first pass was python only, which was the wrong test. reran them with qwen actually on. 97 of 110. ten plans came back unusable and the keyword path filled in. two dispute wordings never retrieved the dispute sentence. one plan just refused "tear up the trades".
