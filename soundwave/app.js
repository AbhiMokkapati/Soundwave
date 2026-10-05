const express = require('express');
const mongoose = require('mongoose');
const bodyParser = require('body-parser');
const cors = require('cors');
const path = require('path');
const crypto = require('crypto');

const app = express();
const port = 3000;

app.use(cors());
app.use(bodyParser.urlencoded({ extended: false }));
app.use(bodyParser.json());
// Serve only the front-end files, not server source or dependencies
app.use((req, res, next) => {
  if (/^\/(node_modules\/|app\.js$|playlists\.js$|package(-lock)?\.json$)/.test(req.path)) {
    return res.sendStatus(404);
  }
  next();
});
app.use(express.static(path.join(__dirname), { dotfiles: 'ignore' }));

// Connect to MongoDB. The connection string is a credential, so it is read
// from the environment and never committed:  MONGODB_URI="mongodb+srv://..."
if (!process.env.MONGODB_URI) {
  console.error('MONGODB_URI is not set; refusing to start without a database.');
  process.exit(1);
}
mongoose.connect(process.env.MONGODB_URI)
  .then(() => {
    console.log('Connected to MongoDB');
  })
  .catch((err) => {
    console.error('Error connecting to MongoDB:', err);
  });

// Define User schema
const userSchema = new mongoose.Schema({
  username: String,
  password: String,
});

// Salted scrypt hashes instead of storing passwords in plaintext
function hashPassword(password, salt = crypto.randomBytes(16).toString('hex')) {
  const hash = crypto.scryptSync(password, salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

function verifyPassword(password, stored) {
  const [salt, hash] = String(stored).split(':');
  if (!salt || !hash) return false;
  const candidate = Buffer.from(hashPassword(password, salt).split(':')[1], 'hex');
  const expected = Buffer.from(hash, 'hex');
  return candidate.length === expected.length && crypto.timingSafeEqual(candidate, expected);
}

// Define User model
const User = mongoose.model('User', userSchema);

// Registration endpoint
app.post('/register', async (req, res) => {
  try {
    const { username, password } = req.body;
    if (typeof username !== 'string' || typeof password !== 'string' || !username || !password) {
      return res.status(400).json({ success: false, message: 'Username and password are required' });
    }

    // Check if the username already exists
    const existingUser = await User.findOne({ username });
    if (existingUser) {
      return res.status(400).json({ success: false, message: 'Username already exists' });
    }

    // Create a new user
    const newUser = new User({ username, password: hashPassword(password) });
    await newUser.save();

    // Log that registration was successful
    console.log('Registration successful');

    // Respond with a success message
    res.json({ success: true, message: 'Registration successful' });
  } catch (error) {
    console.error('Error:', error);
    res.status(500).json({ success: false, message: 'Internal Server Error' });
  }
});

// Login endpoint
app.post('/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    if (typeof username !== 'string' || typeof password !== 'string') {
      return res.status(400).json({ success: false, message: 'Username and password are required' });
    }

    // Check if the user exists and the password matches
    const user = await User.findOne({ username });
    if (user && verifyPassword(password, user.password)) {
      // Respond with a success message
      res.json({ success: true, message: 'Login successful' });
    } else {
      // Respond with an error message
      res.status(401).json({ success: false, message: 'Invalid username or password' });
    }
  } catch (error) {
    console.error('Error:', error);
    res.status(500).json({ success: false, message: 'Internal Server Error' });
  }
});

app.listen(port, () => {
  console.log(`Server is running on port ${port}`);
});




