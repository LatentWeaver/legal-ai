# Codebook Reference

This directory contains reference implementations for constructing a legal corpus codebook.

## Codebook Construction Workflow

1. Filter the land dispute corpus to the selected communities and subcommunities.

2. Extract candidate terms using noun chunks and named entities extraction methods (spaCy, Textacy, RAKE and YAKE).

3. Merge all extracted terms and remove stop words, duplicate terms, and common non informative words.

4. Calculate frequencies of words, phrases and entities across the corpus.

5. Apply frequency adjustment to common unigrams. Each phrase is assigned to the highest frequency unigram it contains and frequent longer phrases are used to discount the unigram frequency. This reduces the influence of common words whose occurrences are already represented by more informative phrases.

6. Rank phrases and entities using the adjusted frequencies.

7. Select the top 3,000 candidate phrases and entities.

8. Manually review and annotate terms based on their relevance to the legal domain and whether they are ambiguous or non ambiguous.

9. Assign legal metadata to relevant terms where appropriate (e.g., Evidence, Issue, Rule, Procedure, etc.).

10. Produce the final annotated legal codebook for downstream analysis and knowledge graph construction.

## Example

Sample codebook from a different project is available below (ASU account required):

NP - https://arizonastateu-my.sharepoint.com/:x:/g/personal/atrive22_sundevils_asu_edu/IQCy2afbj-MmTLfbqwTdTVk_AV4RaPGWvLBjCVenCbC3JJQ?e=HWNHik

NE - https://arizonastateu-my.sharepoint.com/:x:/g/personal/atrive22_sundevils_asu_edu/IQCAlsX9q-nERaedSZLkPFQoAVlLwcIQbSO4bzVJNeK8x_c?e=ez0IwE
