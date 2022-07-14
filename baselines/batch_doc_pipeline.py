
import os
import json
import pandas as pd
import numpy as np
import torch


def read_data(reshuffle_docs = False):

    currentdir = os.getcwd() # ~/MeasEval/baselines

    combopath_txt = os.path.join(currentdir, "../data/raw/combo/text/")
    combopath_annot = os.path.join(currentdir, "../data/raw/combo/tsv/")

    interimpath = os.path.join(currentdir, "../data/interim/")

    if reshuffle_docs == True:
        docIds = []
        combo_txt = {}
        for fn in os.listdir(combopath_txt):
            docIds.append(fn[:-4])
            path = combopath_txt+fn
            with open(path) as textfile:
                    text = textfile.read()
                    #[:-4] strips off the .txt to get the id
                    combo_txt[fn[:-4]] = text

        combo_annot = pd.DataFrame()
        for fn in os.listdir(combopath_annot):
            path = combopath_annot+fn
            file = pd.read_csv(path,delimiter='\t',encoding='utf-8')
            combo_annot = pd.concat([combo_annot, file],ignore_index=True)

        random.shuffle(docIds)

        n_doc = len(docIds)
        split_train = int(np.round(n_doc * percent_to_train))
        split_dev = split_train + int(np.round(n_doc * percent_to_dev))

        docs_train = docIds[:split_train]
        docs_dev = docIds[split_train:split_dev]
        docs_test = docIds[split_dev:]

        train_annot = combo_annot.loc[combo_annot['docId'].isin(docs_train)]
        dev_annot = combo_annot.loc[combo_annot['docId'].isin(docs_dev)]
        test_annot = combo_annot.loc[combo_annot['docId'].isin(docs_test)]

        # save data
        train_annot.to_csv(interimpath+'train_annot.csv')
        dev_annot.to_csv(interimpath+'dev_annot.csv')
        test_annot.to_csv(interimpath+'test_annot.csv')

        train_txt = {d: combo_txt[d] for d in docs_train}
        dev_txt = {d: combo_txt[d] for d in docs_dev}
        test_txt = {d: combo_txt[d] for d in docs_test}
        
        with open(interimpath+'train_txt.json','w') as f:
            json.dump(train_txt, f)
        with open(interimpath+'dev_txt.json','w') as f:
            json.dump(dev_txt, f)
        with open(interimpath+'test_txt.json','w') as f:
            json.dump(test_txt, f)

    else:
        train_annot = pd.read_csv(interimpath+'train_annot.csv')
        dev_annot = pd.read_csv(interimpath+'dev_annot.csv')
        test_annot = pd.read_csv(interimpath+'test_annot.csv')

        with open(interimpath+'train_txt.json','r') as f:
            train_txt = json.load(f)
        with open(interimpath+'dev_txt.json','r') as f:
            dev_txt = json.load(f)
        with open(interimpath+'test_txt.json','r') as f:
            test_txt = json.load(f)
    
    return train_annot, dev_annot, test_annot, train_txt, dev_txt, test_txt




def tokenize_and_align_labels(docs_or_sents, txt, annotation, tokenizer):

    toks_with_labels = []

    for doc in docs_or_sents:
        # print(doc)

        encoded_txt = tokenizer(txt[doc], padding='max_length', max_length=512, truncation=True)
        # print(encoded_txt)

        encoded_tokens = encoded_txt['input_ids']
        # print(encoded_tokens)

        doc_annot = annotation.loc[annotation['docId'] == doc]
        # print(doc_annot)

        annot_spans = np.array(doc_annot[['startOffset','endOffset']])
        # print(f'annot_spans={annot_spans}')

        label_ids = np.full(len(encoded_tokens),0)
        special_ids = tokenizer.all_special_ids
        # print(label_ids.shape)

        for token_idx, token in enumerate(encoded_tokens):
            # decoded_token = tokenizer.decode(token)
            # print(f"token index: {token_idx}")
            # print(f"decoded token: {decoded_token}")

            if token in special_ids:
                label_ids[token_idx] = 0
                # print('special token')

            else:
                token_start_char = encoded_txt.token_to_chars(token_idx).start
                token_end_char = encoded_txt.token_to_chars(token_idx).end
                # print(f"token span: {[token_start_char,token_end_char]}")
                for start, end in annot_spans:
                    if start <= token_start_char <= end:
                        label_ids[token_idx] = 1
                        # print(f'{type} entity found spanning {[start,end]}')
                        break
                    else:
                        label_ids[token_idx] = 0
                        # print("no entity found")
        
        encoded_txt['doc_or_sent_id'] = doc
        encoded_txt['labels'] = list(label_ids)
        toks_with_labels.append(encoded_txt)
    
    # return toks_with_labels
    return pd.DataFrame.from_dict(toks_with_labels)



def batchify(tokenized_dataset, batch_size, device):
    num_examples = int(tokenized_dataset.shape[0] / batch_size)
    batch_sizes = [batch_size for x in range(num_examples)]
    last_batch_size = tokenized_dataset.shape[0] % batch_size
    if last_batch_size:
        batch_sizes.append(last_batch_size)
    # print(batch_sizes)

    batched_dataset = []

    for idx, size in enumerate(batch_sizes):
        start = sum(batch_sizes[:idx])
        end = sum(batch_sizes[:idx]) + size - 1
        # print(start,end,idx)
        input_ids = torch.LongTensor(tokenized_dataset['input_ids'].loc[start:end].tolist()).to(device)
        attention_mask = torch.LongTensor(tokenized_dataset['attention_mask'].loc[start:end].tolist()).to(device)
        labels = torch.LongTensor(tokenized_dataset['labels'].loc[start:end].tolist()).to(device)
        # print(labels.shape)
        doc_or_sent_id = list(tokenized_dataset['doc_or_sent_id'].loc[start:end])
        
        batch = {
            'input_ids':input_ids,
            'labels':labels,
            'attention_mask':attention_mask,
            'doc_or_sent_id':doc_or_sent_id

        }
        
        batched_dataset.append(batch)

    return batched_dataset



