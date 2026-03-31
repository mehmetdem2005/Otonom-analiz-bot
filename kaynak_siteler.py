"""
Onaylı Kaynak Siteleri — 100 Haber + 100 Araştırma/Makale
Sistem YALNIZCA bu listeden veri çeker. Başka siteye girmez.
"""

# ─── 100 HABER SİTESİ ─────────────────────────────────────────────────────────
HABER_SITELERI = [
    # Uluslararası ajanslar & büyük medya
    "https://www.reuters.com",
    "https://apnews.com",
    "https://www.bbc.com/news",
    "https://www.theguardian.com",
    "https://www.nytimes.com",
    "https://www.washingtonpost.com",
    "https://www.wsj.com",
    "https://www.ft.com",
    "https://www.bloomberg.com",
    "https://www.economist.com",
    # Amerika
    "https://www.theatlantic.com",
    "https://www.foreignpolicy.com",
    "https://www.npr.org",
    "https://www.politico.com",
    "https://www.axios.com",
    "https://thehill.com",
    "https://www.vox.com",
    "https://slate.com",
    "https://www.newsweek.com",
    "https://www.usatoday.com",
    "https://www.latimes.com",
    "https://www.chicagotribune.com",
    "https://www.bostonglobe.com",
    "https://abcnews.go.com",
    "https://www.cbsnews.com",
    "https://www.nbcnews.com",
    "https://www.cnn.com",
    "https://www.businessinsider.com",
    "https://www.forbes.com",
    "https://fortune.com",
    "https://www.cnbc.com",
    "https://www.semafor.com",
    "https://theintercept.com",
    "https://www.propublica.org",
    # Teknoloji haberleri
    "https://techcrunch.com",
    "https://www.theverge.com",
    "https://www.wired.com",
    "https://arstechnica.com",
    "https://www.zdnet.com",
    "https://www.cnet.com",
    "https://venturebeat.com",
    "https://thenextweb.com",
    "https://www.infoq.com",
    # Avrupa
    "https://www.aljazeera.com",
    "https://www.dw.com/en",
    "https://www.france24.com/en",
    "https://www.euronews.com",
    "https://www.lemonde.fr",
    "https://www.spiegel.de/international",
    "https://www.independent.co.uk",
    "https://www.thetimes.co.uk",
    "https://www.foreignaffairs.com",
    # Rusya
    "https://tass.com",
    "https://www.rt.com",
    # Orta Doğu & Afrika
    "https://www.arabnews.com",
    "https://www.haaretz.com",
    "https://www.middleeasteye.net",
    # Asya — Japonya, Kore, Hindistan
    "https://asia.nikkei.com",
    "https://www.koreatimes.co.kr",
    "https://www.thehindu.com",
    "https://www.hindustantimes.com",
    "https://www.ndtv.com",
    "https://timesofindia.indiatimes.com",
    "https://economictimes.indiatimes.com",
    "https://www.livemint.com",
    "https://scroll.in",
    "https://theprint.in",
    "https://www.firstpost.com",
    # Pakistan & Güney Asya
    "https://www.dawn.com",
    "https://www.thenews.com.pk",
    # Güneydoğu Asya
    "https://www.straitstimes.com",
    "https://www.bangkokpost.com",
    "https://www.thejakartapost.com",
    "https://www.philstar.com",
    # ÇİN — Çince ve İngilizce kaynaklar
    "https://www.xinhuanet.com/english",
    "https://www.chinadaily.com.cn",
    "https://www.globaltimes.cn",
    "https://www.cgtn.com",
    "https://www.scmp.com",          # South China Morning Post
    "https://www.163.com",           # NetEase
    "https://www.sina.com.cn",
    "https://www.sohu.com",
    "https://www.ifeng.com",
    "https://www.36kr.com",          # Çin startup haberleri
    "https://technode.com",          # Çin teknoloji (İngilizce)
    "https://www.pingwest.com",
    # Genel
    "https://restofworld.org",
    "https://qz.com",
    "https://theconversation.com",
    "https://www.nationthailand.com",
    "https://www.cna.com.tw/news/aipl",
    "https://carnegieendowment.org",
    "https://www.cfr.org",
    "https://www.brookings.edu",
    "https://www.rand.org",
    "https://www.pewresearch.org",
    # Ek haber
    "https://www.motherjones.com",
    "https://www.nationalreview.com",
    "https://reason.com",
    "https://www.outlookindia.com",
]

# ─── 100 ARAŞTIRMA / AKADEMİK / MAKALE SİTESİ ───────────────────────────────
ARASTIRMA_SITELERI = [
    # Preprint sunucuları
    "https://arxiv.org",
    "https://www.biorxiv.org",
    "https://www.medrxiv.org",
    "https://papers.ssrn.com",
    # Büyük bilim dergileri
    "https://www.nature.com",
    "https://www.science.org",
    "https://www.cell.com",
    "https://www.thelancet.com",
    "https://www.nejm.org",
    # Akademik veritabanları
    "https://pubmed.ncbi.nlm.nih.gov",
    "https://www.semanticscholar.org",
    "https://www.researchgate.net",
    "https://www.academia.edu",
    "https://www.jstor.org",
    "https://link.springer.com",
    "https://onlinelibrary.wiley.com",
    "https://www.sciencedirect.com",
    "https://www.tandfonline.com",
    "https://journals.sagepub.com",
    "https://www.frontiersin.org",
    "https://journals.plos.org",
    # Büyük üniversiteler
    "https://news.mit.edu",
    "https://news.stanford.edu",
    "https://news.harvard.edu",
    "https://www.ox.ac.uk/news",
    "https://www.cam.ac.uk/news",
    "https://ethz.ch/en/news-and-events.html",
    "https://www.caltech.edu/about/news",
    "https://www.cmu.edu/news",
    "https://news.berkeley.edu",
    "https://www.imperial.ac.uk/news",
    "https://www.ucl.ac.uk/news",
    # AI / ML araştırma
    "https://openai.com/blog",
    "https://deepmind.google/discover/blog",
    "https://ai.googleblog.com",
    "https://www.microsoft.com/en-us/research/blog",
    "https://ai.meta.com/blog",
    "https://www.anthropic.com/news",
    "https://bair.berkeley.edu/blog",
    "https://distill.pub",
    "https://paperswithcode.com",
    "https://huggingface.co/blog",
    "https://syncedreview.com",
    # AI konferansları
    "https://neurips.cc",
    "https://icml.cc",
    "https://iclr.cc",
    "https://aaai.org/ojs/index.php/AAAI",
    # Mühendislik & CS
    "https://spectrum.ieee.org",
    "https://dl.acm.org",
    "https://stackoverflow.blog",
    "https://www.technologyreview.com",
    # Popüler bilim
    "https://www.scientificamerican.com",
    "https://www.newscientist.com",
    "https://www.quantamagazine.org",
    "https://www.popularmechanics.com",
    "https://www.popsci.com",
    "https://www.discovermagazine.com",
    # Düşünce & felsefe
    "https://aeon.co",
    "https://nautil.us",
    "https://www.edge.org",
    "https://plato.stanford.edu",
    "https://philpapers.org",
    # AI güvenlik & hizalama
    "https://www.lesswrong.com",
    "https://www.alignmentforum.org",
    # Veri & istatistik
    "https://ourworldindata.org",
    "https://www.gapminder.org",
    # Geliştirici & topluluk
    "https://news.ycombinator.com",
    "https://dev.to",
    "https://www.fast.ai",
    "https://www.analyticsvidhya.com",
    "https://www.kdnuggets.com",
    "https://machinelearningmastery.com",
    "https://towardsdatascience.com",
    # ÇİN — AI / Tech araştırma
    "https://www.jiqizhixin.com",    # 机器之心 (Machine Heart)
    "https://www.qbitai.com",        # 量子位
    "https://leiphone.com",          # 雷锋网
    "https://www.leiphone.com/category/ai",
    # Uluslararası kuruluşlar
    "https://www.who.int/news",
    "https://www.un.org/en/desa/news",
    "https://www.worldbank.org/en/news",
    "https://www.imf.org/en/News",
    "https://www.oecd.org/newsroom",
    "https://www.sipri.org/news",
    "https://www.iiss.org/publications",
    # Uzun okuma & analiz
    "https://longreads.com",
    "https://www.nber.org/papers",
    "https://www.brookings.edu/research",
    "https://www.cfr.org/report",
    "https://www.rand.org/pubs/research_reports.html",
    "https://carnegieendowment.org/publications",
    "https://www.pewresearch.org/publications",
    "https://www.foreignaffairs.com/articles",
    # Ek araştırma
    "https://www.nature.com/subjects/machine-learning",
    "https://www.sciencenews.org",
    "https://www.livescience.com",
    "https://phys.org",
    "https://www.eurekalert.org",
    "https://techxplore.com",
    "https://www.sciencealert.com",
    "https://interestingengineering.com",
]

# Tüm siteler birleşik liste
TUM_SITELER = HABER_SITELERI + ARASTIRMA_SITELERI

assert len(HABER_SITELERI) == 100, f"Haber sitesi sayısı: {len(HABER_SITELERI)}"
assert len(ARASTIRMA_SITELERI) == 100, f"Araştırma sitesi sayısı: {len(ARASTIRMA_SITELERI)}"
