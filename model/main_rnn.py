import pandas as pd
import torch
import torch.nn as nn

from torchtext import data
from sklearn.model_selection import train_test_split

sentiments = pd.read_csv('../data/Tweets.csv')
# use only not null text

clean_df = sentiments[sentiments['text'].notnull() &
                      sentiments['airline'].notnull() &
                      sentiments['airline_sentiment'].notnull() &
                      sentiments['tweet_id'].notnull()]
# use only tweet(text), airline, label (airline_sentiment) and tweet id
final_df = clean_df.filter(['tweet_id', 'text', 'airline',
                           'airline_sentiment'], axis=1)
# use only positive and negative sentiment
# row_vals = ['positive', 'negative']
# final_df = final_df.loc[final_df['airline_sentiment'].isin(row_vals)]
# final_df[final_df['airline_sentiment'] != 'neutral']
# use Delta only (this should be a toggle)
# final_df = final_df[final_df['airline'] == 'Delta']

# convert neutral, positive and negative to numeric
# sentiment_map = {'neutral': 0, 'positive': 1, 'negative': -1} 
# final_df['airline_sentiment'] = final_df['airline_sentiment'].map(sentiment_map)
# split into train, test, val (.7, .15, .15)
train_df, testval_df = train_test_split(final_df, test_size=0.3)
test_df, val_df = train_test_split(testval_df, test_size=0.5)

# convert df back to csv, with column names
train_df.to_csv('../data/train.csv', index=False)
test_df.to_csv('../data/test.csv', index=False)
val_df.to_csv('../data/val.csv', index=False)

# load into torchtext
ID = data.Field()
TEXT = data.Field(tokenize='spacy')
SENTIMENT = data.LabelField()
AIRLINE = data.Field()

# access using batch.id, batch.text etc
fields = [('id', ID), ('text', TEXT), ('airline', AIRLINE), ('label', SENTIMENT)]
train_data, valid_data, test_data = data.TabularDataset.splits(path='../data',
                                                               train='train.csv',
                                                               validation='val.csv',
                                                               test='test.csv',
                                                               format='csv',
                                                               fields=fields,
                                                               skip_header=True)
# build iterators
MAX_VOCAB_SIZE = 10_000

ID.build_vocab(train_data)
TEXT.build_vocab(train_data, max_size=MAX_VOCAB_SIZE)
'''
TEXT.build_vocab(train_data,
                 max_size=MAX_VOCAB_SIZE,
                 vectors="glove.twitter.27B.25d",
                 unk_init=torch.Tensor.normal_)
'''
SENTIMENT.build_vocab(train_data)
AIRLINE.build_vocab(train_data)

print(TEXT.vocab.freqs.most_common(20))
# check labels - multiclass
print(SENTIMENT.vocab.stoi)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

BATCH_SIZE = 32

train_iterator, valid_iterator, test_iterator = data.BucketIterator.splits(
    (train_data, valid_data, test_data),
    sort_key=lambda x: x.text,  # sort by text
    batch_size=BATCH_SIZE,
    device=device)


# model
class RNN(nn.Module):
    def __init__(self, input_dim, embedding_dim, hidden_dim, output_dim):
       
        super().__init__()
        self.embedding = nn.Embedding(input_dim, embedding_dim)
        self.rnn = nn.RNN(embedding_dim, hidden_dim)
        self.fc = nn.Linear(hidden_dim, output_dim)
       
    def forward(self, text):

        #text = [sent len, batch size]
        
        embedded = self.embedding(text)
        
        #embedded = [sent len, batch size, emb dim]
        
        output, hidden = self.rnn(embedded)
        
        #output = [sent len, batch size, hid dim]
        #hidden = [1, batch size, hid dim]
        
        assert torch.equal(output[-1,:,:], hidden.squeeze(0))
        
        return self.fc(hidden.squeeze(0))    


INPUT_DIM = len(TEXT.vocab)
EMBEDDING_DIM = 100
HIDDEN_DIM = 256
OUTPUT_DIM = len(SENTIMENT.vocab)

model = RNN(INPUT_DIM, EMBEDDING_DIM, HIDDEN_DIM, OUTPUT_DIM)

def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)

print(f'The model has {count_parameters(model):,} trainable parameters')

import torch.optim as optim

optimizer = optim.SGD(model.parameters(), lr=1e-3)
# criterion = nn.BCEWithLogitsLoss()
criterion = nn.CrossEntropyLoss()
model = model.to(device)
criterion = criterion.to(device)

def binary_accuracy(preds, y):
    """
    Returns accuracy per batch, i.e. if you get 8/10 right, this returns 0.8, NOT 8
    """

    #round predictions to the closest integer
    rounded_preds = torch.round(torch.sigmoid(preds))
    correct = (rounded_preds == y).float() #convert into float for division 
    acc = correct.sum() / len(correct)
    return acc


def categorical_accuracy(preds, y):
    max_preds = preds.argmax(dim=1, keepdim=True)
    correct = max_preds.squeeze(1).eq(y)
    return correct.sum() / torch.FloatTensor([y.shape[0]])


def train(model, iterator, optimizer, criterion):
    
    epoch_loss = 0
    epoch_acc = 0
    
    model.train()
    
    for batch in iterator:
        
        optimizer.zero_grad()
        predictions = model(batch.text)
        loss = criterion(predictions, batch.label)
        acc = categorical_accuracy(predictions, batch.label)
        
        loss.backward()
        
        optimizer.step()
        
        epoch_loss += loss.item()
        epoch_acc += acc.item()
        
    return epoch_loss / len(iterator), epoch_acc / len(iterator)


def evaluate(model, iterator, criterion):
    
    epoch_loss = 0
    epoch_acc = 0
    
    model.eval()
    
    with torch.no_grad():
    
        for batch in iterator:

            predictions = model(batch.text)
            
            loss = criterion(predictions, batch.label)
            
            acc = categorical_accuracy(predictions, batch.label)

            epoch_loss += loss.item()
            epoch_acc += acc.item()
        
    return epoch_loss / len(iterator), epoch_acc / len(iterator)

import time

def epoch_time(start_time, end_time):
    elapsed_time = end_time - start_time
    elapsed_mins = int(elapsed_time / 60)
    elapsed_secs = int(elapsed_time - (elapsed_mins * 60))
    return elapsed_mins, elapsed_secs


N_EPOCHS = 5

best_valid_loss = float('inf')

for epoch in range(N_EPOCHS):

    start_time = time.time()
    
    train_loss, train_acc = train(model, train_iterator, optimizer, criterion)
    valid_loss, valid_acc = evaluate(model, valid_iterator, criterion)
    
    end_time = time.time()

    epoch_mins, epoch_secs = epoch_time(start_time, end_time)
    
    if valid_loss < best_valid_loss:
        best_valid_loss = valid_loss
        torch.save(model.state_dict(), 'tut1-model.pt')
    
    print(f'Epoch: {epoch+1:02} | Epoch Time: {epoch_mins}m {epoch_secs}s')
    print(f'\tTrain Loss: {train_loss:.3f} | Train Acc: {train_acc*100:.2f}%')
    print(f'\t Val. Loss: {valid_loss:.3f} |  Val. Acc: {valid_acc*100:.2f}%')


model.load_state_dict(torch.load('tut1-model.pt'))

test_loss, test_acc = evaluate(model, test_iterator, criterion)

print(f'Test Loss: {test_loss:.3f} | Test Acc: {test_acc*100:.2f}%')
