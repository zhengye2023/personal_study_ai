# Computer Science Notes

## Machine learning basics

Supervised learning uses labeled examples to learn a mapping from input features to targets.
Common tasks include classification and regression.

Train, validation, and test splits help estimate generalization. The training set fits model
parameters, the validation set guides model selection, and the test set should be used only
for final evaluation.

Overfitting means the model performs well on training data but poorly on unseen data.
Typical controls include simpler models, more data, regularization, early stopping, and
cross-validation.

## Neural network basics

A neural network layer computes weighted sums, adds biases, and applies an activation
function. Backpropagation computes gradients by applying the chain rule from the output
layer back to earlier layers.

ReLU is common in hidden layers because it is simple and helps reduce vanishing gradients.
Sigmoid is often used for binary output probabilities, but it can saturate when inputs are
very large or very small.
