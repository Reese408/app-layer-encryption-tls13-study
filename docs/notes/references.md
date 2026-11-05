# Candidate References (verified)

Each entry below was checked against the publisher's or standards body's page on 2026-10-21. Title, authors, venue and date were confirmed there. Entries are written in IEEE style, matching the layout demo. The "Use" column notes what each source supports. Only cite a source for what it actually says. The author should still read every source before citing it.

| # | Reference | Verified at | Use |
|---|---|---|---|
| [1] | E. Rescorla, "The Transport Layer Security (TLS) Protocol Version 1.3," RFC 8446, Aug. 2018. | rfc-editor.org | Hop-by-hop protection; TLS termination model |
| [2] | M. Dworkin, "Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM) and GMAC," NIST SP 800-38D, Nov. 2007. | csrc.nist.gov (a planning note of 2024-03-06 says NIST will revise it) | AES-GCM; random-nonce bound of 2³² invocations (§8.3) |
| [3] | R. Barnes, K. Bhargavan, B. Lipp, and C. Wood, "Hybrid Public Key Encryption," RFC 9180 (Informational, IRTF CFRG), Feb. 2022. | rfc-editor.org | Key encapsulation in place of shared static keys |
| [4] | M. Thomson and C. A. Wood, "Oblivious HTTP," RFC 9458, Jan. 2024. | rfc-editor.org | A standardized encapsulation of HTTP messages: the relay never sees the plaintext request or response |
| [5] | M. Jones and J. Hildebrand, "JSON Web Encryption (JWE)," RFC 7516, May 2015. | rfc-editor.org | The standard JSON envelope format; comparison with the study's custom envelope |
| [6] | M. Thomson, "Encrypted Content-Encoding for HTTP," RFC 8188, Jun. 2017. | rfc-editor.org | Record-based body encryption (`aes128gcm`) that permits incremental processing; relevant to streaming |
| [7] | A. Backman, J. Richer, and M. Sporny, "HTTP Message Signatures," RFC 9421, Feb. 2024. | rfc-editor.org | Integrity without confidentiality, as an alternative |
| [8] | H. Böck, A. Zauner, S. Devlin, J. Somorovsky, and P. Jovanovic, "Nonce-Disrespecting Adversaries: Practical Forgery Attacks on GCM in TLS," in *Proc. 10th USENIX Workshop on Offensive Technologies (WOOT '16)*, Austin, TX, Aug. 2016. (IACR ePrint 2016/475) | usenix.org, eprint.iacr.org | Consequences of GCM nonce reuse observed in the wild |
| [9] | W. Kwon, Z. Li, S. Zhuang, Y. Sheng, L. Zheng, C. H. Yu, J. E. Gonzalez, H. Zhang, and I. Stoica, "Efficient Memory Management for Large Language Model Serving with PagedAttention," in *Proc. 29th ACM Symp. Operating Systems Principles (SOSP '23)*, Koblenz, Germany, Oct. 2023. | arXiv 2309.06180 | High-concurrency LLM serving (vLLM); batching and memory |
| [10] | G.-I. Yu, J. S. Jeong, G.-W. Kim, S. Kim, and B.-G. Chun, "Orca: A Distributed Serving System for Transformer-Based Generative Models," in *Proc. 16th USENIX Symp. Operating Systems Design and Implementation (OSDI '22)*, Carlsbad, CA, Jul. 2022. | usenix.org | Iteration-level (continuous) batching; why per-request latency at scale is set by scheduling |
| [11] | Y. Zhong *et al.*, "DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving," in *Proc. 18th USENIX Symp. Operating Systems Design and Implementation (OSDI '24)*, Santa Clara, CA, Jul. 2024. | usenix.org, arXiv 2401.09670 | Prefill and decode as separate phases; time-to-first-token (TTFT) and time-per-output-token (TPOT) metrics |
| [12] | Apple Security Engineering and Architecture, "Private Cloud Compute: A new frontier for AI privacy in the cloud," Apple Security Research blog, Jun. 10, 2024. https://security.apple.com/blog/private-cloud-compute/ | security.apple.com | A published (vendor) design in which requests are encrypted to attested inference nodes, and load balancers cannot decrypt |
| [13] | S. Yao, J. Zhao, D. Yu, N. Du, I. Shafran, K. Narasimhan, and Y. Cao, "ReAct: Synergizing Reasoning and Acting in Language Models," in *Proc. Int. Conf. Learning Representations (ICLR)*, 2023. | iclr.cc, arXiv 2210.03629 | Agent loops that interleave model calls with tool calls |
| [14] | Model Context Protocol, "Transports," specification version 2025-06-18. https://modelcontextprotocol.io/specification/2025-06-18/basic/transports | modelcontextprotocol.io | Tool calls as JSON-RPC over stdio or Streamable HTTP; many small messages per agent task |
| [15] | R. Redman, "Results: Application-Layer AES-256-GCM over TLS 1.3," research report, source repository, Oct. 2026. | this repository | The study's own measurements |

## Not yet found or verified (do not cite until checked)

- Peer-reviewed measurements of application-layer or message-level encryption overhead in web APIs. The literature review in §2.2 of the paper still needs these. I found no specific paper that I could verify, and none is listed here.
- Vendor documentation for confidential-computing GPUs, for example NVIDIA H100 confidential computing. These are relevant to the "envelope terminates inside the model server" discussion, but I have not verified them.
